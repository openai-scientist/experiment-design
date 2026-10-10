import json

import pytest
from fakes import ScriptedJudge, ScriptedLLM, submit, tool_call
from test_stage_pipeline import (
    normal_responses,
    review_responses,
    source_context,
    source_snapshot,
    supported_design,
)

from experiment_design.config import RunConfig
from experiment_design.contracts import DecisionResponse, FieldAnnotation, Patch, ResearcherDecision
from experiment_design.decisions import apply_decision, interpret, respond
from experiment_design.patch import PatchError, apply_patch, initial_design
from experiment_design.runtime.llm import LLMError
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, advance, approve, run_stage, start_run


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
    from test_decisions import decision_state

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
    responses[1] = submit("submit_design", {"design": design.model_dump(), "rationale": "Draft"})
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) != "REVIEWABLE"
    assert not store.assessment(run).handoff_complete
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


def test_failed_provider_call_has_unknown_usage(tmp_path):
    store = Store(tmp_path / "failed.db")
    deps = Deps(store, ScriptedLLM([LLMError("offline")]), ScriptedJudge([]), RunConfig())
    run = start_run(source_snapshot(), deps, {})
    with pytest.raises(LLMError):
        advance(run, deps)
    usage = store.usage(run)
    assert usage.llm_calls == 1
    assert usage.llm_usage_missing_calls == 1
    assert usage.llm_cache_usage_missing_calls == 1
    assert usage.llm_tokens == 0
    store.close()


def test_handoff_complete_requires_reading_bound_design(tmp_path):
    store = Store(tmp_path / "inspection.db")
    run = store.create_staged_run(source_snapshot(), {})
    store.save_context(run, source_context())
    store.add_version(run, supported_design(), author="design", parent=None)
    store.update_run(run, stage_id="S5")
    deps = Deps(
        store,
        ScriptedLLM(
            [
                tool_call("compute_power", {}),
                normal_responses()[-1],
                tool_call("read_design", {}),
                normal_responses()[-1],
            ]
        ),
        ScriptedJudge([]),
        RunConfig(),
    )
    assert run_stage(run, "S5", deps).status == "COMPLETE"
    assert len(deps.llm.calls) == 4
    assert "inspect" in deps.llm.calls[2][0][-1]["content"].lower()
    store.close()


@pytest.mark.parametrize("change", ["assessment", "applied"])
def test_final_review_uses_current_assessment_and_unanswered_decision(tmp_path, change):
    store = Store(tmp_path / "final.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    advance(run, deps)
    old = store.decisions(run)[-1]
    assessment = store.assessment(run)
    if change == "applied":
        old.status = "APPLIED"
        store.save_decision(old)
    else:
        assessment = assessment.model_copy(
            update={"id": "fresh-assessment", "binding": store.binding(run)}
        )
        store.save_assessment(assessment)
    result = run_stage(run, "S6", deps)
    presented = store.decision(run, result.output_refs[0])
    assert presented.id != old.id and presented.status == "AWAITING_RESEARCHER"
    assert presented.dependencies == [assessment.id]
    store.close()
