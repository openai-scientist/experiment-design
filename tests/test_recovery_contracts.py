import json

import pytest

from experiment_design.config import RunConfig
from experiment_design.contracts import DecisionResponse, FieldAnnotation, Patch, ResearcherDecision
from experiment_design.decisions import apply_decision, interpret, respond
from experiment_design.patch import PatchError, apply_patch, initial_design
from experiment_design.runtime.llm import LLMError
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, advance, approve, run_stage, start_run
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit
from tests.support.scenarios import (
    normal_responses,
    plan_body,
    review_responses,
    source_context,
    source_snapshot,
    supported_design,
)


def test_measurement_cannot_replace_source_owned_primary_outcome():
    context = source_context()
    with pytest.raises(PatchError, match="researcher-owned"):
        apply_patch(
            supported_design(),
            Patch(
                ops=[{"path": "/measurement/primary_metric/name", "value": "attendance"}],
                rationale="Change metric",
            ),
            source_ids={"S1"},
            context=context,
        )


def test_measurement_can_reconcile_to_confirmed_primary_outcome():
    context = source_context()
    before = supported_design()
    before.measurement.primary_metric.name = "attendance"
    corrected = apply_patch(
        before,
        Patch(
            ops=[{"path": "/measurement/primary_metric/name", "value": "math score"}],
            rationale="Restore researcher outcome",
        ),
        source_ids={"S1"},
        context=context,
    )
    assert corrected.measurement.primary_metric.name == corrected.claim.primary_outcome.value


def test_initial_alpha_preserves_source_commitment():
    context = source_context()
    context.field_annotations = {
        "/analysis/alpha": FieldAnnotation(
            status="source-stated", source_ref="S1", source_span="Alpha 0.05"
        )
    }
    with pytest.raises(PatchError, match="researcher-owned"):
        initial_design(context.package, supported_design(), context=context)


def test_margin_interpretation_cannot_change_nested_method(tmp_path):
    from tests.support.scenarios import decision_state

    store, run, context, decision = decision_state(tmp_path)
    answered = respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            text="Two points",
            researcher="PI",
            response_id="margin",
        ),
    )
    changed = context.model_copy(deep=True)
    changed.revision += 1
    changed.package.hypothesis.method = "Another SAP"
    with pytest.raises(ValueError, match="scope"):
        interpret(answered, context, {"authorized": True, "context": changed.model_dump()})
    assert store.context(run) == context
    store.close()


def test_understanding_routing_sees_proposed_decisions(tmp_path):
    def understood(messages, tools):
        binding = json.loads(messages[1]["content"].split("\n")[-1])["binding"]
        decision = ResearcherDecision(
            id="question",
            run_id=run,
            binding=binding,
            problem="Choose estimand",
            affected_paths=["/claim/estimand"],
            dependencies=["power"],
            options=[
                {
                    "id": "ITT",
                    "label": "Intention to treat",
                    "consequences": ["Includes all assigned classes"],
                }
            ],
        )
        return submit(
            "submit_understanding",
            {"context": source_context().model_dump(), "decisions": [decision.model_dump()]},
        )

    store = Store(tmp_path / "routing.db")
    deps = Deps(
        store,
        ScriptedLLM([understood]),
        ScriptedJudge([{"next_action": "ASK_RESEARCHER"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "NEEDS_RESEARCHER"
    state = deps.judge.calls[0][0]
    assert state["proposed_understanding"]["decisions"][0]["problem"] == "Choose estimand"
    assert state["proposed_understanding"]["decisions"][0]["options"][0]["consequences"]
    store.close()


def test_stale_cohort_is_superseded_with_fresh_independent_reviews(tmp_path):
    store = Store(tmp_path / "cohort.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()[:3] + [LLMError("interrupted")]),
        ScriptedJudge([{"next_action": "REVIEW"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    with pytest.raises(LLMError):
        advance(run, deps)
    old = store._artifact_records(
        run,
        "cohort",
        __import__("experiment_design.contracts", fromlist=["ReviewCohort"]).ReviewCohort,
    )[0]
    context = store.context(run).model_copy(deep=True)
    context.revision += 1
    store.save_context(run, context)
    deps.llm = ScriptedLLM([*review_responses(), *normal_responses()[4:]])
    deps.judge = ScriptedJudge([{"next_action": "QUALIFY"}])
    assert advance(run, deps) == "REVIEWABLE"
    assert store.cohort(run, old.id).superseded_by
    assert store.task(old.member_task_ids["methodologist"]).submission is not None
    assert store.task(old.member_task_ids["skeptic"]).submission is None
    store.close()


def test_historical_approved_version_excludes_later_answer(tmp_path):
    store = Store(tmp_path / "history.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    advance(run, deps)
    approve(store, run, 1, "PI")
    decision = store.save_decision(
        ResearcherDecision(
            id="later-margin",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Change margin",
            affected_paths=["/claim/margin"],
        )
    )
    answered = respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            text="Two points",
            researcher="PI",
            response_id="later",
        ),
    )
    context = store.context(run)
    patch = Patch(ops=[{"path": "/claim/margin", "value": 2}], rationale="PI answer")
    store.save_decision(
        interpret(answered, context, {"authorized": True, "patch": patch.model_dump()})
    )
    apply_decision(store, run, decision.id, context=context, patch=patch)
    old = store.publication_snapshot(run, version=1)
    assert old.approval and old.approval.approver == "PI"
    assert not any(d.id == decision.id for d in old.decisions)
    assert old.context.revision == 1 and old.version.design.claim.margin.value == 3
    store.close()


@pytest.mark.parametrize("missing", ["steps", "stop_conditions", "analysis_stopping"])
def test_missing_protocol_and_stopping_cannot_be_complete(tmp_path, missing):
    store = Store(tmp_path / "empty.db")
    responses = normal_responses()
    design = supported_design()
    if missing == "analysis_stopping":
        design.analysis.stopping = None
    else:
        setattr(design.protocol, missing, [])
    responses[1] = submit("submit_design", {"design": plan_body(design), "summary": "Draft"})
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge(
            [{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}, {"next_action": "REVIEW_PLAN"}]
        ),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    assert not store.assessment(run).plan_complete
    assert not store.assessment(run).plan_complete
    with pytest.raises(ValueError):
        approve(store, run, 1, "PI")
    assert not store.approvals(run)
    store.close()


def test_jev_exhaustion_is_budget_stop(tmp_path):
    store = Store(tmp_path / "budget.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}]),
        RunConfig(jev_max_calls=1),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "BUDGET_STOP"
    assert any(
        e.type == "budget.stop" and e.data["reason"] == "jev_call_limit" for e in store.events(run)
    )
    store.close()


def test_unreviewed_qualification_returns_to_critique_without_provider_work(tmp_path):
    from experiment_design.contracts import Design

    store = Store(tmp_path / "inspection.db")
    run = store.create_staged_run(source_snapshot(), {})
    store.save_context(run, source_context())
    store.add_version(
        run, Design.model_validate(plan_body(supported_design())), author="design", parent=None
    )
    store.update_run(run, stage_id="S5")
    deps = Deps(store, ScriptedLLM([]), ScriptedJudge([{"next_action": "CRITIQUE"}]), RunConfig())
    result = run_stage(run, "S5", deps)
    assert result.status == "REVISION_REQUIRED" and store.run(run).stage_id == "S4"
    assert result.issue_refs and deps.llm.calls == []
    assert store.assessment(run).plan_complete is False
    assert store.assessment(run).review_status == "NOT_READY"
    deps.llm = ScriptedLLM(review_responses())
    deps.judge = ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}])
    result = run_stage(run, "S4", deps)
    store.commit_stage(run, result, "S5")
    assert run_stage(run, "S5", deps).status == "COMPLETE"
    assert store.assessment(run).plan_complete is True
    assert len(deps.llm.calls) == 2
    store.close()
