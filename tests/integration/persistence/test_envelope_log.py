import threading

import pytest
from pydantic import ValidationError

from experiment_design.api.contract_models import EventClass, Phase
from experiment_design.persistence.envelope import EnvelopeLog
from tests.integration.review.test_markdown_workflow import store_for


def _append(log, step="s1_snapshot"):
    return log.append(
        "run.step_changed",
        EventClass.lifecycle,
        phase=Phase.drafting,
        step_key=step,
        payload={"from": None},
    )


def test_seq_contiguous_across_reopen(tmp_path):
    _append(EnvelopeLog(tmp_path))
    _append(EnvelopeLog(tmp_path), "s2_framing")
    assert [e.source_seq for e in EnvelopeLog(tmp_path).since(0)] == [1, 2]
    assert [e.source_seq for e in EnvelopeLog(tmp_path).since(1)] == [2]
    assert EnvelopeLog(tmp_path).last_seq == 2


def test_activity_requires_summary(tmp_path):
    with pytest.raises(ValidationError):
        EnvelopeLog(tmp_path).append(
            "exd.agent.accepted",
            EventClass.activity,
            phase=Phase.drafting,
            step_key="s2_framing",
            actor="framing",
        )
    assert EnvelopeLog(tmp_path).last_seq == 0


def test_lifecycle_conditions_are_enforced(tmp_path):
    log = EnvelopeLog(tmp_path)
    with pytest.raises(ValidationError):  # published needs phase=published
        log.append(
            "run.published",
            EventClass.lifecycle,
            phase=Phase.drafting,
            step_key=None,
            payload={"artifact_id": "a1"},
        )
    with pytest.raises(ValidationError):  # failed needs code and message
        log.append("run.failed", EventClass.lifecycle, phase=Phase.failed, step_key=None)
    assert log.last_seq == 0


def test_store_listener_sees_event_and_state(tmp_path):
    store, seen = store_for(tmp_path), []
    store.subscribe(seen.append)
    store.event("route", {"choice": "DESIGN"})
    store.update(stage=2)
    assert seen[0][:2] == ("event", "route")
    assert seen[1][0] == "state" and seen[1][1].stage == 1 and seen[1][2].stage == 2


def test_store_listener_errors_propagate(tmp_path):
    store = store_for(tmp_path)

    def failing(change):
        raise RuntimeError("projection failed")

    store.subscribe(failing)
    with pytest.raises(RuntimeError):
        store.event("route", {})


def test_delivered_cursor_persists_and_never_moves_back(tmp_path):
    log = EnvelopeLog(tmp_path)
    log.mark_delivered(3)
    log.mark_delivered(2)
    assert EnvelopeLog(tmp_path).delivered_seq == 3


def test_concurrent_append_keeps_seq_contiguous(tmp_path):
    def worker():
        log = EnvelopeLog(tmp_path)
        for _ in range(20):
            _append(log)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [e.source_seq for e in EnvelopeLog(tmp_path).since(0)] == list(range(1, 101))


def test_torn_final_append_is_dropped_and_sequence_stays_contiguous(tmp_path):
    log = EnvelopeLog(tmp_path)
    _append(log)
    with log.path.open("ab") as handle:
        handle.write(b'{"source_seq": 2, "type": "run.st')
    assert log.last_seq == 1
    _append(log, "s2_framing")
    assert [e.source_seq for e in EnvelopeLog(tmp_path).since(0)] == [1, 2]


def test_complete_record_missing_its_newline_is_kept(tmp_path):
    log = EnvelopeLog(tmp_path)
    _append(log)
    log.path.write_bytes(log.path.read_bytes().rstrip(b"\n"))
    _append(log, "s2_framing")
    assert [e.source_seq for e in EnvelopeLog(tmp_path).since(0)] == [1, 2]
