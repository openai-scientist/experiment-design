import pytest

from experiment_design.config import RunConfig
from experiment_design.contracts import RunInterrupted
from experiment_design.workflow.engine import advance, respond
from tests.integration.review.test_markdown_workflow import happy_outputs, store_for
from tests.support.providers import Judge, Provider


def _md(*texts):
    return [{"markdown": text} for text in texts]


def test_hold_at_review_stops_before_publish(tmp_path):
    store = store_for(tmp_path)
    state = advance(store, Provider(happy_outputs()), Judge(), RunConfig(), hold_at_review=True)
    assert (state.stage, state.status) == (6, "PENDING_REVIEW")
    assert not (store.path / "stage-07" / "exp_plan.md").exists()


def test_held_run_stays_held_on_repeated_advance(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig(), hold_at_review=True)
    state = advance(store, Provider([]), Judge(), RunConfig(), hold_at_review=True)
    assert (state.stage, state.status) == (6, "PENDING_REVIEW")
    assert not (store.path / "stage-07" / "exp_plan.md").exists()


def test_approve_then_advance_publishes(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig(), hold_at_review=True)
    respond(store, action="APPROVE", text="ok", researcher="r1", revision=store.state.attempt)
    state = advance(store, Provider([]), Judge(), RunConfig(), hold_at_review=True)
    assert state.status == "PUBLISHED"
    assert (store.path / "stage-07" / "exp_plan.md").is_file()


def test_without_hold_the_cli_flow_is_unchanged(tmp_path):
    store = store_for(tmp_path)
    state = advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    assert state.stage == 7 and state.approval == "pending"
    assert (store.path / "stage-07" / "exp_plan.md").is_file()


def test_resume_never_publishes_an_unapproved_held_run(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig(), hold_at_review=True)
    store.update(status="RUNNING")  # a crash left the status behind; stage 6 is complete
    state = advance(store, Provider([]), Judge(), RunConfig(), hold_at_review=True)
    assert (state.stage, state.status) == (6, "PENDING_REVIEW")
    assert not (store.path / "stage-07" / "exp_plan.md").exists()


def test_review_roles_use_review_client(tmp_path):
    stage = Provider(_md("Brief", "Draft"))
    review = Provider(_md("Method", "Skeptic", "Final"))
    advance(store_for(tmp_path), stage, Judge(), RunConfig(), review_llm=review)
    assert len(review.starts) == 3 and len(stage.starts) == 2


def test_agent_started_event_names_the_role(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    roles = [e["data"]["role"] for e in store.events() if e["kind"] == "agent.started"]
    assert roles == ["framing", "design", "methodologist", "skeptic", "final_check"]


@pytest.mark.parametrize("kind", ["cancel", "pause"])
def test_interrupt_request_stops_before_next_agent(tmp_path, kind):
    store = store_for(tmp_path)
    store.request_interrupt(kind)
    provider = Provider(happy_outputs())
    with pytest.raises(RunInterrupted) as info:
        advance(store, provider, Judge(), RunConfig())
    assert info.value.kind == kind
    assert provider.starts == []
    assert store.state.status != "STOPPED"


def test_interrupt_can_be_cleared(tmp_path):
    store = store_for(tmp_path)
    store.request_interrupt("pause")
    assert store.interrupt() == "pause"
    store.clear_interrupt()
    assert store.interrupt() is None
    assert advance(store, Provider(happy_outputs()), Judge(), RunConfig()).review_complete


def test_cancelled_run_does_not_advance(tmp_path):
    store = store_for(tmp_path)
    store.update(status="CANCELLED")
    provider = Provider(happy_outputs())
    assert advance(store, provider, Judge(), RunConfig()).status == "CANCELLED"
    assert provider.starts == []
