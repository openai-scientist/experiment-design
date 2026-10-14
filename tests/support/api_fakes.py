"""Builders for API tests: run requests against the local FakePlatform."""

import json
import re

from experiment_design.api.contract_models import (
    CONTRACT_VERSION,
    I2H_HYPOTHESES,
    SELECTION_HYPOTHESIS_IDS,
    RunCreateRequest,
)
from experiment_design.runtime.llm import LLMResponse, ToolCall
from tests.contract.fake_platform import FakePlatform
from tests.support.providers import Reader, read_documents

SECRET = "sk-test"
TYPESAFE_SECRET = "tsk-test"


def three_hypotheses() -> list[dict]:
    return [
        {
            "id": f"H{n}",
            "statement": f"Statement {n}",
            "falsification_criteria": f"Falsified when outcome {n} fails",
            "predictions": [f"P{n}a", f"P{n}b"],
        }
        for n in (1, 2, 3)
    ]


def make_request(
    platform: FakePlatform,
    *,
    stage_run_id: str,
    mode: str = "light",
    hypotheses: list[dict] | None = None,
    selection: list[str] | None = None,
    review_model: str = "review-m",
    integrations: dict[str, str] | None = None,
    profile: dict | None = None,
) -> RunCreateRequest:
    body = json.dumps(
        {"schema": I2H_HYPOTHESES, "hypotheses": hypotheses or three_hypotheses()}
    ).encode()
    ref = platform.add_artifact(body, schema_id=I2H_HYPOTHESES, engine_key="exd")
    decisions = (
        {"actor": "user", "selection": {SELECTION_HYPOTHESIS_IDS: selection}} if selection else None
    )
    return RunCreateRequest.model_validate(
        {
            "contract_version": CONTRACT_VERSION,
            "platform_run_id": f"pr-{stage_run_id}",
            "stage_run_id": stage_run_id,
            "stage_key": "experiment_plan",
            "interaction_mode": mode,
            "models": {
                "stage": {
                    "provider": "openai_compatible",
                    "base_url": "http://llm.test/v1",
                    "model": "stage-m",
                    "api_key": SECRET,
                },
                "review": {
                    "provider": "openai_compatible",
                    "base_url": "http://llm.test/v1",
                    "model": review_model,
                    "api_key": SECRET,
                },
            },
            "integrations": {"typesafe": TYPESAFE_SECRET} if integrations is None else integrations,
            "budget_usd": "10",
            "profile": profile or {},
            "inputs": {
                "artifacts": [ref.model_dump(by_alias=True)],
                "decisions": decisions,
            },
            "callback_url": platform.callback_url("exd", stage_run_id),
        }
    )


class EndlessProvider(Reader):
    """Reads the required documents, then submits a new Markdown answer every time."""

    def __init__(self, on_complete=lambda: None):
        super().__init__()
        self.answers = 0
        self.on_complete = on_complete

    def complete(self, messages, tools):
        self.on_complete()
        task = json.loads(messages[1]["content"])
        if "input" in task:
            properties = tools[0]["function"]["parameters"]["properties"]
            if "research_type" in properties:
                return LLMResponse(
                    content=None,
                    tokens_in=2,
                    tokens_out=3,
                    tool_calls=[
                        ToolCall(
                            id="infer",
                            name="submit",
                            arguments=json.dumps({"research_type": "unclear"}),
                        )
                    ],
                )
            if "experiments" in properties:
                payload = task["input"]
                heading = next(
                    line
                    for line in payload["plan"].splitlines()
                    if line.startswith("## Experiment")
                )
                body = {
                    "plan_sha256": payload["plan_sha256"],
                    "experiments": [
                        {
                            "experiment_id": "E1",
                            "type": "base",
                            "hypothesis_ids": re.findall(
                                r"^## (H\d+)\b", payload["hypotheses"], re.M
                            )
                            or ["H1"],
                            "datasets": [],
                            "baselines": [],
                            "metrics": [],
                            "controls": [],
                            "seeds": 3,
                            "compute_budget": "equal",
                            "plan_section": heading,
                        }
                    ],
                }
            elif "queries" in properties:
                body = {"queries": [], "tasks": [], "mentions": []}
            elif "choices" in properties:
                body = {"choices": [], "additions": []}
            else:
                raise AssertionError("Unexpected structured model")
            return LLMResponse(
                content=None,
                tokens_in=2,
                tokens_out=3,
                tool_calls=[ToolCall(id="structured", name="submit", arguments=json.dumps(body))],
            )
        calls = self.reading_calls(messages)
        if not calls:
            self.answers += 1
            markdown = (
                f"# Output {self.answers}\n## Experiment E1: Scripted design\n"
                "Use 3 seeds and equal compute.\n"
            )
            body = json.dumps({"markdown": markdown})
            if task.get("task", "").startswith("Role: revision."):
                records = json.loads(read_documents(messages).get("revision_findings", "[]"))
                body = json.dumps(
                    {
                        "markdown": markdown,
                        "responses": [
                            {
                                "finding_id": f["finding_id"],
                                "outcome": "not_fixed",
                                "note": "Scripted fixture did not supply a disposition",
                            }
                            for f in records
                        ],
                    }
                )
            calls = [ToolCall(id=f"submit-{self.answers}", name="submit", arguments=body)]
        return LLMResponse(content=None, tool_calls=calls, tokens_in=2, tokens_out=3)

    def close(self):
        pass


class RecordingFactory:
    """Builds EndlessProviders and records (phase when called, model) for every model call."""

    def __init__(self, probe=lambda: None):
        self.probe = probe
        self.calls: list[tuple[object, str]] = []
        self.keys: list[str] = []

    def __call__(self, cfg, key, role, on_call):
        self.keys.append(key)

        def record():
            self.calls.append((self.probe(), cfg.model))
            on_call({"role": role.value, "model": cfg.model, "outcome": "ok"})

        return EndlessProvider(record)
