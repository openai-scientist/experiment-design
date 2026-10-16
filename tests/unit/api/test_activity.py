import pytest

from experiment_design.api.projector import Projector
from experiment_design.contracts import WorkflowError
from experiment_design.persistence.envelope import EnvelopeLog
from tests.integration.review.test_markdown_workflow import store_for


@pytest.mark.parametrize(
    ("kind", "data", "type", "actor"),
    [
        ("agent.started", {"role": "skeptic", "stage": 4}, "exd.agent.started", "skeptic"),
        ("agent.started", {"role": "final_check", "stage": 5}, "exd.agent.started", "reviewer"),
        ("agent.accepted", {"task": "design:1:3:0"}, "exd.agent.accepted", "designer"),
        (
            "calculation",
            {"result": {"status": "computed", "method_id": "paired_t"}},
            "exd.calculation.computed",
            "system",
        ),
        ("route", {"choice": "DESIGN", "automatic": True}, "exd.route.decided", "system"),
        ("route", {"choice": "REVISE", "automatic": False}, "exd.route.decided", "coordinator"),
        (
            "reframe.objections",
            {"objections": {"critic": "private text"}},
            "exd.review.reframed",
            "system",
        ),
        (
            "revision.pending",
            {"attempt": 1, "plan": "private text", "objections": {}},
            "exd.revision.started",
            "reviser",
        ),
        (
            "researcher.response",
            {
                "researcher": "f573eb2c-7344-4207-b2f2-b06c7de97fd2",
                "action": "ANSWER",
                "option": 2,
                "text": "private text",
            },
            "exd.researcher.responded",
            "researcher",
        ),
    ],
)
def test_projected_activities_are_declared_and_bounded(tmp_path, kind, data, type, actor):
    store = store_for(tmp_path)
    log = EnvelopeLog(store.path)
    store.subscribe(Projector(store, log))
    store.event(kind, data)
    events = [e for e in log.since(0) if e.event_class == "activity"]
    assert len(events) == 1
    event = events[0]
    assert event.type == type and event.actor == actor
    assert event.summary and event.step_key
    assert "private text" not in str(event.payload)
    assert not {"plan", "objections"} & event.payload.keys()


def test_pack_activity_forwarding_and_unknown_activity_rejected(tmp_path):
    store = store_for(tmp_path)
    log = EnvelopeLog(store.path)
    store.subscribe(Projector(store, log))
    store.activity(
        "exd.resources.validated",
        actor="validator",
        summary="Checked metadata",
        step_key="s2b_resources",
        refs=["dataset1"],
        payload={"count": 1},
    )
    event = log.since(0)[0]
    assert event.step_key == "s2b_resources" and event.refs == ["dataset1"]
    assert event.actor == "system"
    with pytest.raises(WorkflowError):
        store.activity("exd.unknown", actor="r", summary="unknown")
