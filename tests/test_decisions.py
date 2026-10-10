import pytest
from conftest import design, package
from test_stages import staged

from experiment_design.contracts import (
    DecisionResponse,
    Patch,
    ResearchContext,
    ResearcherDecision,
)
from experiment_design.decisions import apply_decision, clarification_options, interpret, respond
from experiment_design.runtime.store import Store


def decision_state(tmp_path):
    store = Store(tmp_path / "decision.db")
    run, _ = staged(store)
    context = ResearchContext(
        id="context", revision=1, source_snapshot_id="source-1", package=package()
    )
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
