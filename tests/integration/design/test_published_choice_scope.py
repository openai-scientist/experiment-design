"""Stored scientific choices must be usable without reverse engineering JSON."""

import pytest

from experiment_design.contracts import (
    DecisionResponse,
    Patch,
    ResearcherDecision,
)
from experiment_design.design.decisions import apply_decision, interpret, respond
from tests.support.presentation import study


def test_published_randomization_scope_accepts_only_its_canonical_field(tmp_path):
    store, rid = study(tmp_path)
    try:
        before = store.version(rid)
        context = store.context(rid)
        decision = store.save_decision(
            ResearcherDecision(
                id="randomization-choice",
                run_id=rid,
                binding=store.binding(rid),
                status="AWAITING_RESEARCHER",
                problem="Confirm allocation procedure",
                affected_paths=["/design/randomization"],
            )
        )
        response = DecisionResponse(
            revision=decision.revision,
            action="PROPOSE_ALTERNATIVE",
            text="Stratify classes by school and allocate 1:1",
            researcher="Fixture researcher",
            response_id="fixture-allocation",
        )
        answered = respond(store, rid, decision.id, response)
        interpretation = {
            "authorized": True,
            "ambiguous": False,
            "patch": {
                "ops": [{"path": "/sampling/randomization", "value": response.text}],
                "rationale": "Recorded fixture allocation instruction",
            },
        }
        unrelated = {
            **interpretation,
            "patch": {
                "ops": [{"path": "/claim/margin", "value": 2}],
                "rationale": "Outside scope",
            },
        }
        with pytest.raises(ValueError, match="scope"):
            interpret(answered, context, unrelated)
        interpreted = interpret(answered, context, interpretation)
        store.save_decision(interpreted)
        applied = apply_decision(
            store,
            rid,
            decision.id,
            context=context,
            patch=Patch.model_validate(interpreted.interpretation["patch"]),
        )
        assert store.version(rid).design.sampling.randomization == response.text
        assert store.version(rid).design.claim == before.design.claim
        assert applied.affected_paths == ["/design/randomization"]
        assert applied.responses[-1] == response
        assert applied.application["response_id"] == response.response_id
        assert applied.verification["status"] == "pending"
    finally:
        store.close()
