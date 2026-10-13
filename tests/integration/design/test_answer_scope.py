import pytest

from experiment_design.contracts import DecisionResponse
from experiment_design.design.decisions import interpret, respond


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
