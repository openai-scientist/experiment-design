"""OpenAI-compatible synchronous tool-calling client with bounded retries."""

import time
from collections.abc import Callable
from typing import Self

import httpx
from pydantic import Field, ValidationError, model_validator

from experiment_design.config import LLMConfig
from experiment_design.contracts import Record


class ToolCall(Record):
    id: str
    name: str
    arguments: str


class LLMResponse(Record):
    content: str | None
    tool_calls: list[ToolCall]
    tokens_in: int = Field(ge=0, strict=True)
    tokens_out: int = Field(ge=0, strict=True)
    tokens_cached: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_cache_usage(self) -> Self:
        if self.tokens_cached is not None and self.tokens_cached > self.tokens_in:
            raise ValueError("Cached input tokens cannot exceed total input tokens")
        return self


class LLMError(RuntimeError):
    pass


# Backoff multipliers of retry_base_delay: 2, 5, 15, 30 s at the default, about 52 s in all.
_BACKOFF = (1.0, 2.5, 7.5, 15.0)


class LLMClient:
    def __init__(
        self,
        cfg: LLMConfig,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        *,
        role: str = "stage",
        on_call: Callable[[dict], None] | None = None,
    ):
        self.cfg = cfg
        self.role = role
        self.on_call = on_call
        self.last_attempts: list[dict] = []
        self.max_attempts = cfg.max_retries + 1
        self.client = httpx.Client(
            timeout=cfg.timeout_sec,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def close(self) -> None:
        self.client.close()

    def _report(self, started: float, outcome: str, tokens_in, tokens_out) -> None:
        """Usage and timing only: never the key, messages or content."""
        if self.on_call is not None:
            self.on_call(
                {
                    "role": self.role,
                    "model": self.cfg.model,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "outcome": outcome,
                }
            )

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        self.last_attempts = []
        if not 1 <= self.max_attempts <= self.cfg.max_retries + 1:
            raise LLMError("LLM attempt cap exceeds configured retry allowance")
        for attempt in range(self.max_attempts):
            started = time.monotonic()
            try:
                response = self.client.post(
                    self.cfg.base_url.rstrip("/") + "/chat/completions",
                    json={
                        "model": self.cfg.model,
                        "messages": messages,
                        "tools": tools,
                        "temperature": self.cfg.temperature,
                        "max_tokens": self.cfg.max_tokens,
                        # Workspace agents act only through tools; a plain reply wastes a turn.
                        **({"tool_choice": "required"} if tools else {}),
                    },
                )
                response.raise_for_status()
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                # Timeouts and dropped connections are transient; usage stays unknown.
                code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                # A timeout, a dropped connection or a gateway timeout (504, 524: the provider
                # may have billed the completion) may hide processed work: usage is unknown.
                # Any other error response did no work: zero, so an outage of retried 503s
                # does not use up the review's token cap (R6, live).
                usage = None if code is None or code in (408, 504, 524) else 0
                timed_out = isinstance(exc, httpx.TimeoutException) or code == 408
                self._report(started, "timeout" if timed_out else "error", usage, usage)
                self.last_attempts.append(
                    {
                        "status": "error",
                        "tokens_in": usage,
                        "tokens_out": usage,
                        "tokens_cached": usage,
                        "content": None,
                        "tool_calls": [],
                        "error_type": type(exc).__name__,
                        "status_code": code,
                    }
                )
                retryable = isinstance(exc, httpx.RequestError) or (
                    exc.response.status_code == 429 or exc.response.status_code >= 500
                )
                if not retryable or attempt == self.max_attempts - 1:
                    raise LLMError(f"LLM request failed: {exc}") from exc
                time.sleep(self.cfg.retry_base_delay * _BACKOFF[min(attempt, len(_BACKOFF) - 1)])
                continue
            try:
                body = response.json()
                message = body["choices"][0]["message"]
                parsed = LLMResponse(
                    content=message.get("content"),
                    tool_calls=[
                        ToolCall(
                            id=t["id"],
                            name=t["function"]["name"],
                            arguments=t["function"]["arguments"],
                        )
                        for t in message.get("tool_calls", []) or []
                    ],
                    tokens_in=body["usage"]["prompt_tokens"],
                    tokens_out=body["usage"]["completion_tokens"],
                    tokens_cached=(body["usage"].get("prompt_tokens_details") or {}).get(
                        "cached_tokens"
                    ),
                )
                self._report(started, "ok", parsed.tokens_in, parsed.tokens_out)
                self.last_attempts.append(
                    {
                        "status": "success",
                        "tokens_in": parsed.tokens_in,
                        "tokens_out": parsed.tokens_out,
                        "tokens_cached": parsed.tokens_cached,
                        "error_type": None,
                        "status_code": response.status_code,
                        "content": message.get("content"),
                        "tool_calls": message.get("tool_calls", []),
                    }
                )
                return parsed
            except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
                self._report(started, "error", None, None)
                self.last_attempts.append(
                    {
                        "status": "error",
                        "tokens_in": None,
                        "tokens_out": None,
                        "tokens_cached": None,
                        "content": None,
                        "tool_calls": [],
                        "error_type": type(exc).__name__,
                        "status_code": response.status_code,
                    }
                )
                raise LLMError(f"malformed LLM response: {exc}") from exc
        raise LLMError("LLM retry budget exhausted")
