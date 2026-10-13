import logging

import pytest
from fastapi.testclient import TestClient

from experiment_design.api.app import create_app
from experiment_design.api.contract_models import Capabilities, Health, Phase
from experiment_design.api.observability import SECRETS, SecretFilter
from tests.integration.api.test_run_manager import _manager, _wait
from tests.support.api_fakes import SECRET, make_request


@pytest.fixture
def served(tmp_path):
    manager, platform, _ = _manager(tmp_path)
    app = create_app(manager.settings, manager=manager)
    return TestClient(app), manager, platform


def _headers(platform):
    return {"X-Service-Key": platform.service_key}


def test_health_is_public_and_detailed_with_key(served):
    client, _, platform = served
    bare = client.get("/health")
    assert bare.status_code == 200
    body = Health.model_validate(bare.json())
    assert body.checks is None and body.last_error is None
    assert "checks" not in bare.json()
    assert Health.model_validate(client.get("/health", headers=_headers(platform)).json()).checks


def test_capabilities_valid_and_keyed(served):
    client, _, platform = served
    assert client.get("/capabilities").status_code == 401
    caps = Capabilities.model_validate(
        client.get("/capabilities", headers=_headers(platform)).json()
    )
    assert caps.engine_key == "exd" and caps.stage_keys == ["experiment_plan"]
    assert [o.primary for o in caps.output_schemas] == [True]
    assert [s.order for s in caps.steps] == list(range(1, len(caps.steps) + 1))
    assert caps.integrations == ["typesafe"] and caps.profiles


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/runs"),
        ("get", "/runs/x"),
        ("get", "/runs/x/events"),
        ("post", "/runs/x/gates/g"),
        ("post", "/runs/x/pause"),
        ("post", "/runs/x/resume"),
        ("post", "/runs/x/cancel"),
    ],
)
@pytest.mark.parametrize("headers", [{}, {"X-Service-Key": "wrong"}])
def test_every_route_but_health_needs_the_service_key(served, method, path, headers):
    client, _, _ = served
    response = getattr(client, method)(path, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"code": "SERVICE_KEY_INVALID", "message": response.json()["message"]}


def test_create_run_and_duplicate(served):
    client, manager, platform = served
    body = make_request(platform, stage_run_id="sr1").model_dump(mode="json", by_alias=True)
    body["models"]["stage"]["api_key"] = SECRET
    body["models"]["review"]["api_key"] = SECRET
    body["integrations"] = {"typesafe": "tsk-test"}
    first = client.post("/runs", json=body, headers=_headers(platform))
    assert first.status_code == 201 and first.json()["created"] is True
    run_id = first.json()["engine_run_id"]
    again = client.post("/runs", json=body, headers=_headers(platform))
    assert again.status_code == 200 and again.json()["engine_run_id"] == run_id
    state = _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    got = client.get(f"/runs/{run_id}", headers=_headers(platform)).json()
    assert got["open_gate"]["gate_id"] == state.open_gate.gate_id
    events = client.get(f"/runs/{run_id}/events?after=0&limit=2", headers=_headers(platform)).json()
    assert [e["source_seq"] for e in events["events"]] == [1, 2]
    assert SECRET not in client.get(f"/runs/{run_id}", headers=_headers(platform)).text
    approve = {"action": "approve", "actor": "user", "decided_by": "u1"}
    done = client.post(
        f"/runs/{run_id}/gates/{state.open_gate.gate_id}", json=approve, headers=_headers(platform)
    )
    assert done.status_code == 200
    repeated = client.post(
        f"/runs/{run_id}/gates/{state.open_gate.gate_id}", json=approve, headers=_headers(platform)
    )
    assert repeated.status_code == 200
    other = client.post(f"/runs/{run_id}/gates/g-unknown", json=approve, headers=_headers(platform))
    assert other.status_code == 409 and other.json()["code"] == "GATE_NOT_OPEN"


def test_error_bodies_have_code_and_message(served):
    client, _, platform = served
    missing = client.get("/runs/nope", headers=_headers(platform))
    assert missing.status_code == 404 and set(missing.json()) == {"code", "message"}
    assert missing.json()["code"] == "RUN_NOT_FOUND"
    assert client.get("/runs/x/events?after=-1", headers=_headers(platform)).status_code == 422
    assert client.get("/runs/x/events?limit=501", headers=_headers(platform)).status_code == 422


def test_validation_error_does_not_echo_api_key(served):
    client, _, platform = served
    body = make_request(platform, stage_run_id="sr1").model_dump(mode="json", by_alias=True)
    body["models"]["stage"]["api_key"] = SECRET
    body["models"]["stage"]["unexpected"] = SECRET
    body.pop("stage_run_id")
    response = client.post("/runs", json=body, headers=_headers(platform))
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
    assert SECRET not in response.text
    assert all(set(d) == {"loc", "msg", "type"} for d in response.json()["details"])


def test_service_key_is_compared_in_constant_time(served, monkeypatch):
    client, _, platform = served
    used = []
    real = __import__("hmac").compare_digest
    monkeypatch.setattr("hmac.compare_digest", lambda a, b: used.append(1) or real(a, b))
    client.get("/capabilities", headers=_headers(platform))
    assert used


def test_secret_filter_masks_message_args_and_exceptions(caplog):
    filt = SecretFilter()
    filt.add("sk-secret")
    record = logging.LogRecord("t", logging.ERROR, "f", 1, "key %s failed", ("sk-secret",), None)
    assert filt.filter(record) and record.getMessage() == "key *** failed"
    try:
        raise ValueError("bad sk-secret")
    except ValueError:
        import sys

        rec = logging.LogRecord("t", logging.ERROR, "f", 1, "boom", None, sys.exc_info())
    filt.filter(rec)
    assert "sk-secret" not in (rec.exc_text or "") and "***" in rec.exc_text


def test_created_run_registers_its_keys_with_the_log_filter(served):
    client, manager, platform = served
    manager.create(make_request(platform, stage_run_id="sr9"))
    assert SECRET in SECRETS._secrets
