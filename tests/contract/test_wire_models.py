"""Engine models agree with the vendored contract documents in both directions."""

import hashlib
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from experiment_design.api import contract_models as cm
from tests.contract.conftest import VENDOR, load_json

SCHEMAS = sorted(path.stem for path in (VENDOR / "schema").glob("*.json"))
EXAMPLES = sorted(path.stem for path in (VENDOR / "examples").glob("*.json"))
EVENT_PAYLOADS = {
    "run.phase_changed": cm.PhaseChanged,
    "run.step_changed": cm.StepChanged,
    "run.gate_opened": cm.Gate,
    "run.gate_closed": cm.GateClosed,
    "review.completed": cm.ReviewCompleted,
    "artifact.delivered": cm.ArtifactDelivered,
    "run.published": cm.RunPublished,
    "run.failed": cm.FailureInfo,
    "run.cancelled": cm.EmptyPayload,
    "run.cost": cm.RunCost,
}


def _schema(name: str) -> dict:
    return load_json(VENDOR / "schema" / f"{name}.json")


def _validate(instance, name: str) -> None:
    jsonschema.Draft202012Validator(_schema(name)).validate(instance)


def _dump(model) -> dict:
    return model.model_dump(mode="json", by_alias=True)


def test_contract_version_matches_vendored_readme():
    readme = (VENDOR / "README.md").read_text(encoding="utf-8")
    assert f"Contract version: **{cm.CONTRACT_VERSION}**" in readme
    assert cm.is_compatible("1.7.2") and not cm.is_compatible("2.0.0")


def test_every_vendored_schema_has_an_engine_model():
    assert set(SCHEMAS) == set(cm.MODELS)


@pytest.mark.parametrize("name", [n for n in EXAMPLES if not n.startswith("event-")])
def test_example_parses_and_round_trips_through_schema(name):
    example = load_json(VENDOR / "examples" / f"{name}.json")
    _validate(example, name)
    model = cm.MODELS[name].model_validate(example)
    _validate(_dump(model), name)


@pytest.mark.parametrize("name", [n for n in EXAMPLES if n.startswith("event-")])
def test_event_example_parses_and_payload_matches_type(name):
    example = load_json(VENDOR / "examples" / f"{name}.json")
    _validate(example, "EventEnvelope")
    event = cm.EventEnvelope.model_validate(example)
    _validate(_dump(event), "EventEnvelope")
    payload_model = EVENT_PAYLOADS.get(event.type)
    if payload_model is not None:
        payload_model.model_validate(event.payload)


def test_every_schema_has_an_example():
    assert {n for n in EXAMPLES if not n.startswith("event-")} == set(SCHEMAS)


@pytest.mark.parametrize("name", ["Gate", "Capabilities", "RunState", "Health"])
def test_receivers_ignore_unknown_fields(name):
    example = load_json(VENDOR / "examples" / f"{name}.json") | {"added_later": 1}
    cm.MODELS[name].model_validate(example)


@pytest.mark.parametrize("name", ["ModelConfig", "RunCreateRequest", "TelemetryRecord"])
def test_secret_and_telemetry_models_reject_unknown_fields(name):
    example = load_json(VENDOR / "examples" / f"{name}.json") | {"added_later": 1}
    with pytest.raises(ValidationError):
        cm.MODELS[name].model_validate(example)


def test_review_model_must_differ_from_stage_model():
    stage = {"provider": "openai_compatible", "model": "m"}
    with pytest.raises(ValidationError, match="review model must differ"):
        cm.Models.model_validate({"stage": stage, "review": dict(stage)})
    cm.Models.model_validate({"stage": stage, "review": {"provider": "bedrock", "model": "m"}})


def test_secret_values_are_not_dumped():
    request = cm.RunCreateRequest.model_validate(
        load_json(VENDOR / "examples" / "RunCreateRequest.json")
        | {"integrations": {"typesafe": "real-secret"}}
    )
    assert "real-secret" not in request.model_dump_json()
    assert "real-secret" not in repr(request)


def test_vendor_matches_management():
    management = Path(__file__).resolve().parents[3] / "management" / "docs" / "contract"
    if not management.is_dir():
        pytest.skip("management/docs/contract is not adjacent")

    def digest(root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(
                path.read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.relative_to(root).as_posix() != "SOURCE"
        }

    assert digest(VENDOR) == digest(management), (
        "re-copy management/docs/contract and update SOURCE"
    )
