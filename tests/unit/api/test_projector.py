import pytest

from experiment_design.api.contract_models import EventEnvelope, InteractionMode, Phase
from experiment_design.api.mapping import open_gate
from experiment_design.api.projector import Projector
from experiment_design.persistence.envelope import EnvelopeLog
from tests.integration.review.test_markdown_workflow import store_for


def _wired(tmp_path):
    store = store_for(tmp_path)
    out: list[EventEnvelope] = []
    projector = Projector(store, EnvelopeLog(store.path), on_append=out.append)
    store.subscribe(projector)
    return store, projector, out


def test_start_announces_phase_and_first_step(tmp_path):
    _, projector, out = _wired(tmp_path)
    projector.start()
    assert [(e.type, e.phase, e.step_key) for e in out] == [
        ("run.phase_changed", Phase.drafting, "s1_snapshot"),
        ("run.step_changed", Phase.drafting, "s1_snapshot"),
    ]
    assert out[0].payload == {"from": None}


def test_role_drives_review_phase(tmp_path):
    store, _, out = _wired(tmp_path)
    store.update(stage=4)
    store.event("agent.started", {"role": "methodologist", "stage": 4})
    store.event("agent.started", {"role": "revision", "stage": 4})
    changes = [e for e in out if e.type == "run.phase_changed"]
    assert [e.phase for e in changes] == [Phase.reviewing, Phase.revising]
    assert [e.payload["from"] for e in changes] == ["drafting", "reviewing"]


def test_step_change_event(tmp_path):
    store, _, out = _wired(tmp_path)
    store.update(stage=2)
    assert out[-1].type == "run.step_changed" and out[-1].step_key == "s2_framing"
    assert out[-1].payload == {"from": "s1_snapshot"}


def test_step_entered_event_changes_step(tmp_path):
    store, _, out = _wired(tmp_path)
    store.event("step.entered", {"step_key": "s2b_resources"})
    assert (out[-1].type, out[-1].step_key) == ("run.step_changed", "s2b_resources")


def test_unmapped_events_emit_nothing(tmp_path):
    store, _, out = _wired(tmp_path)
    store.event("route", {"choice": "DESIGN"})
    store.update(llm_tokens=5)
    assert out == []


def test_failure_is_announced_once_with_its_code(tmp_path):
    store, _, out = _wired(tmp_path)
    store.update(status="STOPPED")
    store.update(llm_tokens=1)
    failed = [e for e in out if e.type == "run.failed"]
    assert len(failed) == 1 and failed[0].phase is Phase.failed
    assert failed[0].payload["code"] == "workflow_stopped"


def test_published_status_emits_no_event_here(tmp_path):
    store, _, out = _wired(tmp_path)
    store.update(stage=7, status="PUBLISHED")
    assert not any(e.type == "run.published" for e in out)


def test_seq_contiguous(tmp_path):
    store, projector, out = _wired(tmp_path)
    projector.start()
    store.update(stage=2)
    store.update(stage=4)
    store.event("agent.started", {"role": "revision", "stage": 4})
    store.update(stage=6, status="PENDING_REVIEW")
    assert [e.source_seq for e in out] == list(range(1, len(out) + 1))
    assert [e.source_seq for e in EnvelopeLog(store.path).since(0)] == [e.source_seq for e in out]


def test_sink_failure_surfaces_from_store(tmp_path):
    store, _, _ = _wired(tmp_path)

    def broken(event):
        raise RuntimeError("callback sink down")

    store.subscribe(Projector(store, EnvelopeLog(store.path), on_append=broken))
    with pytest.raises(RuntimeError):
        store.update(stage=2)


def test_open_gate_only_while_waiting_at_stage_six(tmp_path):
    store = store_for(tmp_path)
    assert open_gate(store, artifact_ids=["a1"], mode=InteractionMode.light) is None
    store.update(stage=6, status="PENDING_REVIEW")
    gate = open_gate(store, artifact_ids=["a1"], mode=InteractionMode.light)
    assert gate.kind.value == "artifact_review" and gate.step_key == "s6_researcher_review"
    assert gate.gate_id == "g1-s6-review" and gate.artifact_ids == ["a1"]
