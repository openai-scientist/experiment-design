from experiment_design.config import RunConfig
from experiment_design.persistence.store import Store
from experiment_design.workflow import Deps, advance, start_run
from tests.support.providers import ScriptedJudge, ScriptedLLM
from tests.support.scenarios import (
    normal_responses,
    source_snapshot,
)


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
