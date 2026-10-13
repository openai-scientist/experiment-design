"""TypeSafe Choice adapter: workflow choices stay constrained to legal options."""

import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient

from experiment_design.config import ConfigError, JevConfig


@dataclass(frozen=True)
class Question:
    instructions: str
    options: dict[str, str]


@dataclass(frozen=True)
class Decision:
    choice: str
    probabilities: dict[str, float]
    confidence: float
    latency_ms: int = 0
    tokens: int | None = None


class JudgeError(RuntimeError):
    pass


class JudgeBudgetStop(JudgeError):
    pass


class Judge(Protocol):
    def decide(self, state: dict, questions: dict[str, Question]) -> dict[str, Decision]: ...


class TypeSafeJudge:
    def __init__(self, cfg: JevConfig, client: Any | None = None):
        self.cfg = cfg
        self.last_attempts: list[dict] = []
        self.max_attempts = 3
        if client is None:
            key = os.environ.get(cfg.api_key_env)
            if not key:
                raise ConfigError(f"missing API key environment variable {cfg.api_key_env}")
            client = TypeSafeClient(api_key=key, retry=RetryPolicy(max_retries=0))
        self.client = client

    def decide(self, state: dict, questions: dict[str, Question]) -> dict[str, Decision]:
        self.last_attempts = []
        if not 1 <= self.max_attempts <= 3:
            raise JudgeError("JEV attempt cap must be from 1 to 3")
        kwargs = {
            "state": state,
            "questions": {
                k: Choice(instructions=q.instructions, criteria=q.options)
                for k, q in questions.items()
            },
        }
        if self.cfg.model is not None:
            kwargs["model"] = self.cfg.model
        started = time.perf_counter()
        for attempt in range(self.max_attempts):
            try:
                response = self.client.system_one(**kwargs)
                break
            except Exception as exc:
                self.last_attempts.append(
                    {
                        "status": "error",
                        "tokens_in": None,
                        "tokens_out": None,
                        "tokens_cached": None,
                        "error_type": type(exc).__name__,
                        "status_code": None,
                    }
                )
                if attempt == self.max_attempts - 1:
                    raise JudgeError(
                        f"JEV failed after {self.max_attempts} attempts: {exc}"
                    ) from exc
        latency = int((time.perf_counter() - started) * 1000)
        usage = getattr(response, "usage", None)
        tokens_in = getattr(usage, "input_tokens", None)
        tokens_out = getattr(usage, "output_tokens", None)
        telemetry = {
            "status": "success",
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_cached": None,
            "error_type": None,
            "status_code": None,
        }
        self.last_attempts.append(telemetry)
        if any(
            value is not None and (type(value) is not int or value < 0)
            for value in (tokens_in, tokens_out)
        ):
            telemetry.update(
                status="error", tokens_in=None, tokens_out=None, error_type="JudgeError"
            )
            raise JudgeError("JEV usage must contain nonnegative integer token counts")
        tokens = (
            tokens_in + tokens_out if tokens_in is not None and tokens_out is not None else None
        )
        decisions = {}
        for index, (key, question) in enumerate(questions.items()):
            try:
                answer = response.choices[key]
                if answer.choice not in question.options:
                    telemetry.update(status="error", error_type="JudgeError")
                    raise JudgeError(f"JEV returned illegal choice {answer.choice!r} for {key}")
                decisions[key] = Decision(
                    answer.choice,
                    dict(answer.probabilities),
                    answer.confidence,
                    latency,
                    tokens if index == 0 else None,
                )
            except (KeyError, AttributeError, TypeError) as exc:
                telemetry.update(status="error", error_type=type(exc).__name__)
                raise JudgeError(f"JEV response missing or malformed question {key}") from exc
        telemetry["choices"] = {
            key: {
                "choice": decision.choice,
                "probabilities": decision.probabilities,
                "confidence": decision.confidence,
            }
            for key, decision in decisions.items()
        }
        return decisions
