"""Builders for API tests: run requests against the local FakePlatform."""

import json

from experiment_design.api.contract_models import (
    CONTRACT_VERSION,
    I2H_HYPOTHESES,
    SELECTION_HYPOTHESIS_IDS,
    RunCreateRequest,
)
from tests.contract.fake_platform import FakePlatform

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
