import hashlib

import httpx
import pytest

from experiment_design.api.callbacks import CallbackError, PlatformClient, StageClosed
from experiment_design.api.contract_models import (
    EXD_EXP_PLAN,
    ArtifactMeta,
    EventClass,
    Phase,
    TelemetryRecord,
)
from experiment_design.persistence.envelope import EnvelopeLog
from tests.contract.fake_platform import FakePlatform


def _log_with(tmp_path, count):
    log = EnvelopeLog(tmp_path)
    for n in range(count):
        log.append(
            "run.step_changed",
            EventClass.lifecycle,
            phase=Phase.drafting,
            step_key=f"s{n + 1}",
            payload={"from": None},
        )
    return log


def _client(platform, **kwargs):
    return PlatformClient(
        platform.callback_url("exd", "sr1"),
        platform.service_key,
        transport=platform.transport(),
        sleep=lambda seconds: None,
        **kwargs,
    )


def test_events_delivered_in_order(tmp_path):
    platform, log = FakePlatform(), _log_with(tmp_path, 3)
    assert _client(platform).flush_events(log) == 3
    assert [e.source_seq for e in platform.events["sr1"]] == [1, 2, 3]
    assert log.delivered_seq == 3
    assert _client(platform).flush_events(log) == 0


def test_large_backlog_is_sent_in_batches(tmp_path):
    platform, log = FakePlatform(), _log_with(tmp_path, 250)
    assert _client(platform).flush_events(log) == 250
    assert [e.source_seq for e in platform.events["sr1"]] == list(range(1, 251))
    assert len([r for r in platform.requests if r.url.path.endswith("/events")]) == 3


def test_retry_after_503_does_not_duplicate(tmp_path):
    platform, log = FakePlatform(), _log_with(tmp_path, 3)
    platform.fail_next = [503, 429]
    assert _client(platform).flush_events(log) == 3
    assert [e.source_seq for e in platform.events["sr1"]] == [1, 2, 3]


def test_lost_acknowledgement_is_resent_without_duplicates(tmp_path):
    platform, log = FakePlatform(), _log_with(tmp_path, 3)
    platform.drop_event_acks = 1  # Platform stored the batch but the answer was a 503
    assert _client(platform).flush_events(log) == 3
    assert [e.source_seq for e in platform.events["sr1"]] == [1, 2, 3]


def test_retries_are_bounded(tmp_path):
    platform, log = FakePlatform(), _log_with(tmp_path, 1)
    platform.fail_next = [503] * 10
    with pytest.raises(CallbackError, match="HTTP 503"):
        _client(platform, attempts=3).flush_events(log)
    assert log.delivered_seq == 0
    assert len(platform.requests) == 3


def test_network_error_is_retried(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"accepted": 1, "skipped": 0})

    log = _log_with(tmp_path, 1)
    client = PlatformClient(
        "http://platform.test/cb", "k", transport=httpx.MockTransport(handler), sleep=lambda s: None
    )
    assert client.flush_events(log) == 1 and len(calls) == 2


def test_event_gap_resends_from_the_platform_cursor_and_keeps_the_run(tmp_path):
    platform, log = FakePlatform(), _log_with(tmp_path, 3)
    log.mark_delivered(2)  # the engine believes seq 1-2 arrived; Platform holds none
    assert _client(platform).flush_events(log) == 3
    assert [e.source_seq for e in platform.events["sr1"]] == [1, 2, 3]
    assert log.delivered_seq == 3


def test_stage_closed_stops_delivery(tmp_path):
    platform, log = FakePlatform(), _log_with(tmp_path, 2)
    platform.closed.add("sr1")
    with pytest.raises(StageClosed):
        _client(platform).flush_events(log)
    assert log.delivered_seq == 0


def test_wrong_service_key_is_rejected_without_retry(tmp_path):
    platform, log = FakePlatform(), _log_with(tmp_path, 1)
    client = PlatformClient(
        platform.callback_url("exd", "sr1"),
        "wrong-key",
        transport=platform.transport(),
        sleep=lambda s: None,
    )
    with pytest.raises(CallbackError, match="HTTP 401 SERVICE_KEY_INVALID"):
        client.flush_events(log)
    assert len(platform.requests) == 1
    assert "wrong-key" not in repr(client)


def _meta(content: bytes, **kwargs) -> ArtifactMeta:
    return ArtifactMeta(
        schema_id=EXD_EXP_PLAN,
        version=1,
        filename="exp_plan.md",
        media_type="text/markdown",
        sha256=hashlib.sha256(content).hexdigest(),
        **kwargs,
    )


def test_artifact_roundtrip(tmp_path):
    platform, content = FakePlatform(), "Plan é\n".encode()
    meta = _meta(content, primary=True, metadata={"review_passed": True})
    artifact_id = _client(platform).push_artifact(meta, content)
    stored = platform.artifacts["sr1"][0]
    assert artifact_id == stored.artifact_id
    assert stored.content == content
    assert stored.meta.schema_id == EXD_EXP_PLAN and stored.meta.primary is True
    assert stored.meta.metadata == {"review_passed": True}


def test_artifact_retried_after_server_error(tmp_path):
    platform, content = FakePlatform(), b"plan"
    platform.fail_next = [502]
    _client(platform).push_artifact(_meta(content), content)
    assert len(platform.artifacts["sr1"]) == 1


def test_artifact_hash_mismatch_is_not_retried(tmp_path):
    platform = FakePlatform()
    with pytest.raises(CallbackError, match="SHA256_MISMATCH"):
        _client(platform).push_artifact(_meta(b"other"), b"plan")
    assert len(platform.requests) == 1


def test_telemetry_is_delivered(tmp_path):
    platform = FakePlatform()
    record = TelemetryRecord(
        occurred_at="2026-10-14T00:00:00Z",
        level="INFO",
        message="step done",
        platform_run_id="pr-1",
        stage_run_id="sr1",
        engine_run_id="e1",
    )
    _client(platform).send_telemetry([record])
    assert platform.telemetry["sr1"][0].message == "step done"
