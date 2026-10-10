import pytest
from conftest import design
from test_stages import staged

from experiment_design.contracts import (
    DecisionResponse,
    Patch,
    ResearcherDecision,
)
from experiment_design.decisions import apply_decision, clarification_options, interpret, respond
from experiment_design.runtime.store import Store


def decision_state(tmp_path):
    store = Store(tmp_path / "decision.db")
    run, _ = staged(store)
    from test_stage_pipeline import source_context

    context = source_context()
    store.save_context(run, context)
    store.add_version(run, design(), author="design", parent=None)
    d = store.save_decision(
        ResearcherDecision(
            id="D1",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Choose margin",
            affected_paths=["/claim/margin"],
        )
    )
    return store, run, context, d


def test_alternative_records_raw_intent_and_applies_once(tmp_path):
    store, run, context, decision = decision_state(tmp_path)
    before = store.version(run)
    response = DecisionResponse(
        revision=1,
        action="PROPOSE_ALTERNATIVE",
        text="Use two points, not the suggested options",
        researcher="researcher",
        response_id="response-1",
    )
    answered = respond(store, run, decision.id, response)
    assert answered.id == decision.id
    assert answered.responses[-1].text == response.text
    assert store.version(run).design_hash == before.design_hash
    interpreted = interpret(
        answered,
        context,
        {
            "authorized": True,
            "ambiguous": False,
            "patch": {
                "ops": [{"path": "/claim/margin", "value": 2}],
                "rationale": "Researcher alternative",
            },
        },
    )
    store.save_decision(interpreted)
    proposed = Patch.model_validate(interpreted.interpretation["patch"])
    applied = apply_decision(store, run, decision.id, context=context, patch=proposed)
    assert applied.status == "APPLIED"
    assert applied.application["response_id"] == response.response_id
    assert store.version(run).design.claim.margin.value == 2
    assert store.context(run).package.inference.margin == 2
    assert store.context(run).revision == 2
    assert applied.verification["status"] == "pending"
    count = len(store.events(run))
    apply_decision(store, run, decision.id, context=context, patch=proposed)
    assert len(store.events(run)) == count
    store.close()


@pytest.mark.parametrize("mode", ["stale", "ambiguous", "scope", "duplicate"])
def test_invalid_application_does_not_mutate_design(tmp_path, mode):
    store, run, context, decision = decision_state(tmp_path)
    before = store.version(run)
    response = DecisionResponse(
        revision=2 if mode == "stale" else 1,
        action="PROPOSE_ALTERNATIVE",
        text="New margin",
        researcher="researcher",
        response_id="response-1",
    )
    if mode == "stale":
        with pytest.raises(ValueError, match="revision"):
            respond(store, run, decision.id, response)
    else:
        answered = respond(store, run, decision.id, response)
        if mode == "duplicate":
            count = len(store.events(run))
            respond(store, run, decision.id, response)
            assert len(store.events(run)) == count
        else:
            result = {
                "authorized": mode != "ambiguous",
                "ambiguous": mode == "ambiguous",
                "patch": {
                    "ops": [
                        {
                            "path": "/claim/margin" if mode == "ambiguous" else "/analysis/alpha",
                            "value": 2,
                        }
                    ],
                    "rationale": "Interpretation",
                },
            }
            if mode == "scope":
                with pytest.raises(ValueError, match="scope"):
                    interpret(answered, context, result)
            else:
                unresolved = interpret(answered, context, result)
                store.save_decision(unresolved)
                with pytest.raises(ValueError, match="interpretation"):
                    apply_decision(
                        store,
                        run,
                        decision.id,
                        context=context,
                        patch=Patch(ops=[], rationale="Wait"),
                    )
    assert store.version(run).design_hash == before.design_hash
    store.close()


def test_just_in_time_accepts_either_trigger_without_exhaustive_options(tmp_path):
    store, run, context, decision = decision_state(tmp_path)
    assert "ASK_RESEARCHER" in clarification_options(
        decision, informative_options=True, blocks_required_work=False
    )
    assert "ASK_RESEARCHER" in clarification_options(
        decision, informative_options=False, blocks_required_work=True
    )
    assert "ASK_RESEARCHER" not in clarification_options(
        decision, informative_options=False, blocks_required_work=False
    )
    store.close()


def test_response_rejects_stale_binding_and_second_answer(tmp_path):
    store, run, context, decision = decision_state(tmp_path)
    response = DecisionResponse(
        revision=1,
        action="PROPOSE_ALTERNATIVE",
        text="Two points",
        researcher="PI",
        response_id="one",
    )
    respond(store, run, decision.id, response)
    before = store.events(run)
    with pytest.raises(ValueError, match="awaiting"):
        respond(store, run, decision.id, response.model_copy(update={"response_id": "two"}))
    assert store.events(run) == before
    decision = store.decision(run, decision.id)
    decision.status = "AWAITING_RESEARCHER"
    decision.revision += 1
    store.save_decision(decision)
    store.save_context(run, context.model_copy(update={"revision": 2}))
    before = store.events(run)
    with pytest.raises(ValueError, match="binding"):
        respond(
            store,
            run,
            decision.id,
            response.model_copy(update={"revision": 2, "response_id": "three"}),
        )
    assert store.events(run) == before
    store.close()


def test_context_interpretation_cannot_change_unapproved_claim(tmp_path):
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
            response_id="one",
        ),
    )
    changed = context.model_copy(deep=True)
    changed.revision = 2
    changed.package.inference.estimand = "A different population effect"
    with pytest.raises(ValueError, match="scope"):
        interpret(
            answered,
            context,
            {"authorized": True, "ambiguous": False, "context": changed.model_dump(mode="json")},
        )
    assert store.context(run) == context
    store.close()


@pytest.mark.parametrize(
    "path,value", [("/analysis/model", "Pooled t test of cluster means"), ("/analysis/alpha", 0.05)]
)
def test_authorized_sap_change_records_confirmed_commitment(tmp_path, path, value):
    store, run, context, _ = decision_state(tmp_path)
    decision = store.save_decision(
        ResearcherDecision(
            id="SAP",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Revise SAP",
            affected_paths=[path],
        )
    )
    answered = respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            text=f"Use {value}",
            researcher="PI",
            response_id="SAP-reply",
        ),
    )
    proposed = Patch(ops=[{"path": path, "value": value}], rationale="PI SAP")
    store.save_decision(
        interpret(answered, context, {"authorized": True, "patch": proposed.model_dump()})
    )
    apply_decision(store, run, decision.id, context=context, patch=proposed)
    updated = store.context(run)
    assert updated.field_annotations[path].status == "researcher-confirmed"
    assert updated.field_annotations[path].value == value
    if path == "/analysis/model":
        assert updated.package.hypothesis.method == value
    store.close()


def test_researcher_evidence_can_supply_source_quantity_for_independent_review(tmp_path):
    from test_stage_pipeline import source_context, source_snapshot, supported_design

    from experiment_design.agents.tools import build_tools
    from experiment_design.contracts import AgentTask, TaskBudget
    from experiment_design.review import commit_revision, review_context, start_cohort
    from experiment_design.runtime.store import TaskRecord

    store = Store(tmp_path / "evidence.db")
    run = store.create_staged_run(source_snapshot(), {})
    context = source_context()
    store.save_context(run, context)
    store.add_version(run, supported_design(), author="design", parent=None)
    decision = store.save_decision(
        ResearcherDecision(
            id="SD",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Confirm SD",
            affected_paths=["/scenarios/0/sd"],
        )
    )
    answered = respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            text="Observed standard deviation 12 points",
            researcher="PI",
            response_id="SD-reply",
        ),
    )
    source_ref = "researcher:SD:SD-reply"
    proposed = Patch(
        ops=[
            {
                "path": "/scenarios/0/sd",
                "value": {
                    "value": 12,
                    "unit": "points",
                    "provenance": "source",
                    "source_ref": source_ref,
                    "source_span": "Observed standard deviation 12 points",
                },
            }
        ],
        rationale="Observed SD",
    )
    store.save_decision(
        interpret(answered, context, {"authorized": True, "patch": proposed.model_dump()})
    )
    apply_decision(store, run, decision.id, context=context, patch=proposed)
    cohort = start_cohort(store, run, store.binding(run))
    task = AgentTask(
        id=cohort.member_task_ids["skeptic"],
        binding=cohort.binding,
        cohort_id=cohort.id,
        stage_id="S4",
        objective="Review",
        package_ref="source",
        design_version=2,
        allowed_tools=[],
        budget=TaskBudget(max_turns=3, max_tokens=1000),
        exit_conditions=["Submit"],
    )
    store.save_task(run, TaskRecord(task=task))
    ctx = review_context(store, run, task)
    hits = build_tools(ctx)["lookup_evidence"].handler({"query": "Observed standard deviation"})
    assert any(h["source_id"] == source_ref for h in hits)
    result = build_tools(ctx)["compute_power"].handler({})[0]
    assert result["status"] == "computed" and result["normalized_inputs"]["sd"] == 12
    updated = commit_revision(
        store,
        run,
        parent=2,
        patch=Patch(
            ops=[{"path": "/protocol/resources", "value": ["District test records"]}],
            rationale="Operational refinement",
        ),
        issue_ids=[],
    )
    assert updated.design.scenarios[0].sd.source_ref == source_ref
    store.close()
