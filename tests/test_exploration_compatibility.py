import pytest

from experiment_design.config import RunConfig
from experiment_design.contracts import AgentResult, Observation
from experiment_design.exploration import ExplorationSelection
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, WorkflowError, advance, approve, start_run
from tests.support.builders import design, package
from tests.support.exploration import (
    advance_framing,
    exploration_payload,
    exploration_task,
    framed_run,
)
from tests.support.history import create_historical_run
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit
from tests.support.scenarios import normal_responses, source_context, source_snapshot


def test_normal_staged_run_keeps_plan_workflow(tmp_path):
    store = Store(tmp_path / "exd.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    rid = start_run(source_snapshot(), deps, {})
    assert not store.is_exploration_run(rid)
    assert advance(rid, deps) == "REVIEWABLE"
    snapshot = store.publication_snapshot(rid)
    assert snapshot.version.version == store.version(rid).version
    assert "exploration_policy" not in snapshot.model_dump()
    assert store.exploration_snapshot(rid) is None
    store.close()


@pytest.mark.parametrize("legacy", [True, False])
def test_existing_normal_run_rejects_exploration_mutations(tmp_path, legacy):
    store = Store(tmp_path / "exd.db")
    normal = (
        create_historical_run(store, package(), {})
        if legacy
        else store.create_staged_run(source_snapshot(), {})
    )
    exploration = framed_run(store)
    task = store.save_exploration_task(exploration, exploration_task(store, exploration))
    before = store.events(normal)
    for mutation in [
        lambda: store.save_exploration_task(normal, task),
        lambda: store.save_exploration_selection(
            normal,
            ExplorationSelection(task_id=task.id, binding=task.binding, choice="recommend:probe-a"),
        ),
        lambda: store.freeze_exploration(normal, task.id),
    ]:
        with pytest.raises(ValueError, match="exploration"):
            mutation()
        assert store.events(normal) == before
    assert not store.is_exploration_run(normal)
    store.close()


def test_new_context_invalidates_unfrozen_candidate_selection(tmp_path):
    store = Store(tmp_path / "exd.db")
    rid = framed_run(store)
    task = store.save_exploration_task(rid, exploration_task(store, rid))
    store.record_submission(
        task.id,
        AgentResult(findings_or_changes=exploration_payload(), completion_status="completed"),
    )
    context = source_context()
    context.revision = 2
    store.save_context(rid, context)
    before = store.events(rid)
    with pytest.raises(ValueError, match="binding"):
        store.save_exploration_selection(
            rid,
            ExplorationSelection(task_id=task.id, binding=task.binding, choice="recommend:probe-a"),
        )
    assert store.events(rid) == before
    assert store.exploration_selection(rid) is None

    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import respond

    decision = store.save_decision(
        ResearcherDecision(
            id="clarify",
            run_id=rid,
            binding=store.binding(rid),
            status="AWAITING_RESEARCHER",
            problem="What measurement is available?",
            affected_paths=["/measurement/data_collection"],
        )
    )
    respond(
        store,
        rid,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            response_id="reply1",
            researcher="PI",
            text="Repeated blinded measurements are available",
        ),
    )
    fresh = exploration_task(store, rid).model_copy(update={"id": "clarified-exploration"})
    store.save_exploration_task(rid, fresh)
    payload = exploration_payload(source_ref="researcher:clarify:reply1")
    payload["candidates"][0]["evidence_refs"] = ["researcher:clarify:reply1"]
    store.record_submission(
        fresh.id,
        AgentResult(
            findings_or_changes=payload,
            completion_status="completed",
        ),
    )
    store.save_exploration_selection(
        rid,
        ExplorationSelection(
            task_id=fresh.id,
            binding=fresh.binding,
            choice="recommend:probe-a",
        ),
    )
    frozen = store.freeze_exploration(rid, fresh.id)
    assert frozen.submission.intent_source_refs == ["researcher:clarify:reply1"]
    assert frozen.submission.candidates[0].evidence_refs == ["researcher:clarify:reply1"]
    store.close()


def test_candidate_snapshot_cannot_promote_historical_approval(tmp_path):
    import json
    from pathlib import Path

    from experiment_design.contracts import Design
    from experiment_design.exploration import ExplorationSnapshot
    from experiment_design.patch import design_hash

    legacy = json.loads(
        (Path(__file__).parent / "fixtures/pre_cutover_plan_v2.json").read_text(encoding="utf-8")
    )
    assert design_hash(Design.model_validate(legacy["design"])) == legacy["design_hash"]
    assert (
        ExplorationSnapshot.model_validate(legacy["standalone_exploration"]).model_dump(mode="json")
        == legacy["standalone_exploration"]
    )
    store = Store(tmp_path / "exd.db")
    historical = create_historical_run(store, package(), {})
    store.add_version(historical, design(), author="fixture", parent=None)
    history = store.events(historical)
    deps = Deps(
        store,
        ScriptedLLM([submit("submit_exploration", exploration_payload())]),
        ScriptedJudge([]),
        RunConfig(),
    )
    rid = framed_run(store)
    assert advance_framing(rid, deps) == "RUNNING"
    before = store.events(rid)
    for operation in [
        lambda: approve(store, rid, 1, "PI"),
    ]:
        with pytest.raises(WorkflowError, match="version"):
            operation()
        assert store.events(rid) == before
    assert store.approvals(rid) == []
    assert store.events(historical) == history
    assert store.version(rid) is None
    store.close()


def test_store_revalidates_accepted_submission_nested_refs(tmp_path):
    store = Store(tmp_path / "exd.db")
    rid = framed_run(store)
    task = store.save_exploration_task(rid, exploration_task(store, rid))
    store.save_observation(
        rid,
        Observation(
            id="peer:read",
            task_id="peer",
            binding=task.binding,
            tool_name="read_sources",
            arguments={},
            result={},
            accepted=True,
            evidence_ref="tool:peer:read",
        ),
    )
    payload = exploration_payload()
    payload["priorities"][0]["evidence_refs"] = ["tool:peer:read"]
    store.record_submission(
        task.id, AgentResult(findings_or_changes=payload, completion_status="completed")
    )
    before = store.events(rid)
    with pytest.raises(ValueError, match="refs"):
        store.save_exploration_selection(
            rid,
            ExplorationSelection(task_id=task.id, binding=task.binding, choice="recommend:probe-a"),
        )
    assert store.events(rid) == before
    assert store.exploration_snapshot(rid) is None
    store.close()
