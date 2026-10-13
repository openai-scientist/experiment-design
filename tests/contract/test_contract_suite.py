"""The mandatory engine behavior tests of the vendored contract README, run against exd over HTTP.

Names are the stable identifiers from ``vendor/README.md``. Every payload the engine sends or
returns is also validated against the vendored JSON Schema.
"""

import json

import jsonschema
import pytest
from pydantic import ValidationError

from experiment_design.api.contract_models import (
    CONTRACT_VERSION,
    Capabilities,
    Health,
    LlmCall,
    TelemetryRecord,
    is_compatible,
)
from tests.contract.conftest import VENDOR, load_json
from tests.support.api_fakes import SECRET, TYPESAFE_SECRET

FINDING = {
    "finding_id": "R1",
    "anchor": {"locator": "exp_plan.md#design"},
    "severity": "major",
    "issue": "Describe the control arm",
    "author": "researcher",
}
APPROVE = {"action": "approve", "actor": "user", "decided_by": "u1"}


def _valid(instance, name: str) -> None:
    schema = load_json(VENDOR / "schema" / f"{name}.json")
    jsonschema.Draft202012Validator(schema).validate(instance)


def _wire(model) -> dict:
    return model.model_dump(mode="json", by_alias=True)


def _to_review_gate(h, stage_run_id="sr1", **kwargs):
    created = h.create(stage_run_id, **kwargs)
    assert created.status_code == 201
    run_id = created.json()["engine_run_id"]
    return run_id, h.wait(run_id, "awaiting_researcher", gate=True)


def test_capabilities_valid(contract_harness):
    h = contract_harness
    body = h.client.get("/capabilities", headers=h.headers).json()
    _valid(body, "Capabilities")
    caps = Capabilities.model_validate(body)
    assert caps.engine_key == "exd" and is_compatible(caps.contract_version)
    assert caps.contract_version.split(".")[0] == CONTRACT_VERSION.split(".")[0]
    assert caps.profiles  # the base profile
    assert [o.primary for o in caps.output_schemas].count(True) == 1


def test_runs_with_base_profile(contract_harness):
    h = contract_harness
    _, state = _to_review_gate(h, profile={})
    assert state["phase"] == "awaiting_researcher"
    assert state["open_gate"]["kind"] == "artifact_review"


def test_health_liveness_without_key(contract_harness):
    h = contract_harness
    bare = h.client.get("/health")
    assert bare.status_code == 200
    _valid(bare.json(), "Health")
    assert {"status", "engine_version", "contract_version", "active_runs"} <= set(bare.json())
    assert "checks" not in bare.json() and "last_error" not in bare.json()
    detailed = h.client.get("/health", headers=h.headers)
    _valid(detailed.json(), "Health")
    assert Health.model_validate(detailed.json()).checks


def test_runs_require_service_key(contract_harness):
    h = contract_harness
    assert h.client.post("/runs", json=h.request()).status_code == 401
    wrong = h.client.post("/runs", json=h.request(), headers={"X-Service-Key": "bad"})
    assert wrong.status_code == 401


def test_create_run_idempotent(contract_harness):
    h = contract_harness
    first = h.create()
    second = h.create()
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["engine_run_id"] == second.json()["engine_run_id"]
    assert second.json()["created"] is False
    h.wait(first.json()["engine_run_id"], "awaiting_researcher", gate=True)
    assert len(h.factory.keys) == 2  # one stage and one review client: no duplicated work


def test_reaches_review_gate(contract_harness):
    h = contract_harness
    _, state = _to_review_gate(h)
    _valid(state, "RunState")
    assert state["phase"] == "awaiting_researcher" and state["waiting_input"] is True
    assert state["open_gate"]["kind"] == "artifact_review"


def test_lifecycle_events_ordered(contract_harness):
    h = contract_harness
    run_id, _ = _to_review_gate(h)
    h.settle(run_id)
    events = h.events()
    assert [e.source_seq for e in events] == list(range(1, len(events) + 1))
    for event in events:
        _valid(_wire(event), "EventEnvelope")
    phases = [e.phase.value for e in events if e.type == "run.phase_changed"]
    assert phases[0] == "drafting"
    assert phases.index("reviewing") < phases.index("awaiting_researcher")


def test_activity_events_declared(contract_harness):
    h = contract_harness
    run_id, _ = _to_review_gate(h)
    h.settle(run_id)
    caps = Capabilities.model_validate(h.client.get("/capabilities", headers=h.headers).json())
    for event in h.events():
        if event.event_class.value == "activity":
            assert event.type in caps.activity_types
            assert event.step_key and event.actor and event.summary


def test_review_uses_review_model(contract_harness):
    h = contract_harness
    _to_review_gate(h, review_model="review-m")
    reviewing = [model for phase, model in h.factory.calls if phase.value == "reviewing"]
    assert reviewing and set(reviewing) == {"review-m"}


def test_comment_returns_to_review_gate(contract_harness):
    h = contract_harness
    run_id, first = _to_review_gate(h)
    comment = {"action": "comment", "actor": "user", "decided_by": "u2", "findings": [FINDING]}
    assert h.answer(run_id, first["open_gate"]["gate_id"], comment).status_code == 200
    second = h.wait(run_id, "awaiting_researcher", gate=True)
    assert second["open_gate"]["gate_id"] != first["open_gate"]["gate_id"]
    h.settle(run_id)
    phases = [e.phase.value for e in h.events() if e.type == "run.phase_changed"]
    after = phases[phases.index("awaiting_researcher") + 1 :]
    assert after[:2] == ["revising", "reviewing"] and after[-1] == "awaiting_researcher"


def test_approve_publishes_primary_artifact(contract_harness):
    h = contract_harness
    run_id, state = _to_review_gate(h)
    assert h.answer(run_id, state["open_gate"]["gate_id"], APPROVE).status_code == 200
    h.wait(run_id, "published")
    h.settle(run_id)
    caps = Capabilities.model_validate(h.client.get("/capabilities", headers=h.headers).json())
    primary_schema = next(o.schema_id for o in caps.output_schemas if o.primary)
    primary = [a for a in h.platform.artifacts["sr1"] if a.meta.primary]
    assert len(primary) == 1 and primary[0].meta.schema_id == primary_schema
    _valid(_wire(primary[0].meta), "ArtifactMeta")
    published = h.events()[-1]
    assert published.type == "run.published"
    assert published.payload["artifact_id"] == primary[0].artifact_id


def test_cancel(contract_harness):
    h = contract_harness
    run_id, _ = _to_review_gate(h)
    cancelled = h.client.post(f"/runs/{run_id}/cancel", headers=h.headers)
    assert cancelled.status_code == 200 and cancelled.json()["phase"] == "cancelled"
    h.settle(run_id)
    assert h.events()[-1].type == "run.cancelled"


def test_no_secret_leak(contract_harness):
    h = contract_harness
    run_id, state = _to_review_gate(h)
    seen = [
        json.dumps(state),
        h.client.get(f"/runs/{run_id}/events?after=0&limit=500", headers=h.headers).text,
    ]
    h.answer(run_id, state["open_gate"]["gate_id"], APPROVE)
    h.wait(run_id, "published")
    h.settle(run_id)
    seen.append(h.client.get(f"/runs/{run_id}", headers=h.headers).text)
    seen.extend(r.content.decode("utf-8", errors="replace") for r in h.platform.requests)
    seen.extend(h.logs.lines)
    seen.extend(
        p.read_bytes().decode("utf-8", errors="replace")
        for p in h.runs_root.rglob("*")
        if p.is_file()
    )
    for secret in (SECRET, TYPESAFE_SECRET):
        assert [text for text in seen if secret in text] == []


def test_telemetry_well_formed(contract_harness):
    h = contract_harness
    run_id, _ = _to_review_gate(h)
    h.settle(run_id)
    for record in h.platform.telemetry.get("sr1", []):
        _valid(record.model_dump(mode="json", by_alias=True, exclude_none=True), "TelemetryRecord")
        assert record.platform_run_id == "pr-sr1"
        if record.llm and record.phase and record.phase.value == "reviewing":
            assert record.llm.role.value == "review"
    base = {
        "occurred_at": "2026-10-14T00:00:00Z",
        "level": "INFO",
        "message": "m",
        "platform_run_id": "p",
        "stage_run_id": "s",
        "engine_run_id": "e",
    }
    TelemetryRecord.model_validate(base)
    for extra in ("prompt", "response", "api_key"):
        with pytest.raises(ValidationError):
            TelemetryRecord.model_validate({**base, extra: "x"})
    call = {
        "role": "stage",
        "provider": "p",
        "model": "m",
        "input_tokens": 1,
        "output_tokens": 1,
        "latency_ms": 1,
        "outcome": "ok",
    }
    LlmCall.model_validate(call)
    with pytest.raises(ValidationError):
        LlmCall.model_validate({**call, "prompt": "x"})


@pytest.mark.skip(reason="restart support lands in S4.4; the README requires it only then")
def test_restart_fails_open_runs(contract_harness):
    raise AssertionError("implemented with S4.4")


def test_pause_then_resume_continues(contract_harness):
    h = contract_harness
    run_id, first = _to_review_gate(h)
    paused = h.client.post(f"/runs/{run_id}/pause", headers=h.headers)
    assert paused.status_code == 200 and paused.json()["phase"] == "paused"
    assert h.client.post(f"/runs/{run_id}/resume", headers=h.headers).status_code == 200
    state = h.wait(run_id, "awaiting_researcher", gate=True)
    assert state["open_gate"]["gate_id"] == first["open_gate"]["gate_id"]
    h.settle(run_id)
    assert [e.type for e in h.events()].count("run.gate_opened") == 1


def test_error_body_shape(contract_harness):
    h = contract_harness
    run_id, _ = _to_review_gate(h)
    response = h.answer(run_id, "g-never-opened", APPROVE)
    assert response.status_code == 409
    assert (
        set(response.json()) == {"code", "message"} and response.json()["code"] == "GATE_NOT_OPEN"
    )


def test_event_gap_triggers_resend_not_cancel(contract_harness):
    h = contract_harness
    run_id, first = _to_review_gate(h)
    h.settle(run_id)
    del h.platform.events["sr1"][-2:]  # Platform lost its tail, so the next batch has a gap
    comment = {"action": "comment", "actor": "user", "decided_by": "u2", "findings": [FINDING]}
    h.answer(run_id, first["open_gate"]["gate_id"], comment)
    h.wait(run_id, "awaiting_researcher", gate=True)
    h.settle(run_id)
    assert h.state(run_id)["phase"] == "awaiting_researcher"
    assert [e.source_seq for e in h.events()] == list(range(1, len(h.events()) + 1))


def test_repeated_gate_answer_is_idempotent(contract_harness):
    h = contract_harness
    run_id, state = _to_review_gate(h)
    gate_id = state["open_gate"]["gate_id"]
    assert h.answer(run_id, gate_id, APPROVE).status_code == 200
    h.wait(run_id, "published")
    h.settle(run_id)
    published = [e for e in h.events() if e.type == "run.published"]
    again = h.answer(run_id, gate_id, APPROVE)
    assert again.status_code == 200 and again.json()["phase"] == "published"
    other = h.answer(run_id, gate_id, {**APPROVE, "action": "comment", "findings": [FINDING]})
    assert other.status_code == 409 and other.json()["code"] == "GATE_NOT_OPEN"
    h.settle(run_id)
    assert [e for e in h.events() if e.type == "run.published"] == published


def test_validation_error_does_not_echo_api_key(contract_harness):
    h = contract_harness
    body = h.request()
    body["models"]["stage"]["api_key"] = "distinct-secret-xyz"
    body["models"]["stage"]["unexpected"] = "distinct-secret-xyz"
    del body["stage_run_id"]
    response = h.client.post("/runs", json=body, headers=h.headers)
    assert response.status_code == 422
    assert "distinct-secret-xyz" not in response.text
    assert all(set(d) == {"loc", "msg", "type"} for d in response.json()["details"])
