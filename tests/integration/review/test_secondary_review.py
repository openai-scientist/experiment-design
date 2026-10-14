import pytest

from experiment_design.contracts import WorkflowError
from experiment_design.workflow.engine import _ask_review, respond, secondary_review
from tests.integration.review.test_markdown_workflow import store_for


@pytest.mark.parametrize("action", ["APPROVE", "REQUEST_CHANGES"])
def test_secondary_decision_keeps_stage_attempt_and_exact_comment(tmp_path, action):
    store = store_for(tmp_path)
    store.update(stage=3)
    store.write(3, "resources.json", "{}")
    _ask_review(
        store,
        step_key="s2b_resources",
        stage=3,
        name="resources.json",
        schema_id="exd.resources/v1",
        prompt="Review resources",
    )
    with pytest.raises(WorkflowError, match="secondary|REVIEW"):
        respond(store, action="ANSWER", text="yes", researcher="r", revision=1)
    assert not any(e["kind"] == "researcher.response" for e in store.events())
    respond(store, action=action, text=" exact comment ", researcher="r", revision=1)
    assert (store.state.status, store.state.stage, store.state.attempt) == ("RUNNING", 3, 1)
    record = secondary_review(store, "s2b_resources")
    assert record["action"] == action and record["text"] == " exact comment "
    assert not store.valid(7)
    _ask_review(
        store,
        step_key="s2b_resources",
        stage=3,
        name="resources.json",
        schema_id="exd.resources/v1",
        prompt="Review revised resources",
    )
    assert secondary_review(store, "s2b_resources") is None


def test_api_secondary_gate_stays_in_drafting_and_references_uploaded_artifact(
    tmp_path, monkeypatch
):
    from tests.integration.api.test_run_manager import _manager
    from tests.support.api_fakes import make_request

    def request_review(store, *args, **kwargs):
        store.update(stage=3)
        store.write(3, "resources.json", "{}")
        _ask_review(
            store,
            step_key="s2b_resources",
            stage=3,
            name="resources.json",
            schema_id="exd.resources/v1",
            prompt="Review resources",
        )
        return store.state

    monkeypatch.setattr("experiment_design.api.runs.advance", request_review)
    manager, platform, _ = _manager(tmp_path)
    run_id = manager.create(
        make_request(platform, stage_run_id="secondary", mode="full")
    ).engine_run_id
    assert manager.wait_idle(run_id)
    state = manager.state(run_id)
    assert state.phase == "drafting" and state.open_gate.kind == "artifact_review"
    assert state.open_gate.step_key == "s2b_resources"
    artifact = platform.artifacts["secondary"][0]
    assert artifact.artifact_id in state.open_gate.artifact_ids
    assert artifact.meta.schema_id == "exd.resources/v1" and artifact.meta.primary is False
