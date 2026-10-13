import pytest

from experiment_design.config import RunConfig
from experiment_design.contracts import DecisionResponse, ResearcherDecision
from experiment_design.design.decisions import respond
from experiment_design.persistence.store import Store
from experiment_design.workflow import Deps, WorkflowError, advance
from tests.support.builders import package
from tests.support.history import create_historical_run
from tests.support.providers import ScriptedJudge, ScriptedLLM
from tests.support.scenarios import (
    source_snapshot,
)


@pytest.mark.parametrize("schema_version", [1, 2])
def test_legacy_resume_refused_without_mutation(tmp_path, schema_version):

    store = Store(tmp_path / "legacy.db")
    if schema_version == 1:
        run = create_historical_run(store, package(), {})
    else:
        run = store.create_staged_run(source_snapshot(), {})
        historical = store.run(run).model_copy(update={"plan_contract": None})
        store.connection.execute(
            "UPDATE runs SET data=? WHERE run_id=?", (historical.model_dump_json(), run)
        )
        store.save_decision(
            ResearcherDecision(
                id="old-question",
                run_id=run,
                binding=store.binding(run),
                status="AWAITING_RESEARCHER",
                problem="Old clarification",
                affected_paths=[],
            )
        )
    before = store.events(run)
    with pytest.raises(WorkflowError, match="schema|read/export only"):
        advance(run, Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig()))
    assert store.events(run) == before
    with pytest.raises(ValueError, match="read/export only"):
        respond(
            store,
            run,
            "old-question",
            DecisionResponse(
                revision=1,
                action="PROPOSE_ALTERNATIVE",
                text="New intent",
                researcher="PI",
                response_id="reply",
            ),
        )
    assert store.events(run) == before
    store.close()
