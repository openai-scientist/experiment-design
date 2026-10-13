import pytest

from experiment_design.config import RunConfig
from experiment_design.persistence.store import Store
from experiment_design.workflow import Deps
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit
from tests.support.scenarios import (
    plan_body,
    source_context,
    supported_design,
)


def test_context_change_during_drafting_cannot_commit_a_stale_initial_design(tmp_path):
    from experiment_design.workflow import run_stage
    from tests.support.scenarios import staged

    store = Store(tmp_path / "stale-draft.db")
    run, _ = staged(store)
    context = source_context()
    store.save_context(run, context)
    store.update_run(run, stage_id="S3")

    def compose(messages, tools):
        changed = context.model_copy(deep=True)
        changed.revision += 1
        changed.package.hypothesis.method = "New researcher method"
        store.save_context(run, changed)
        return submit(
            "submit_design",
            {"design": plan_body(supported_design()), "summary": "Earlier context"},
        )

    deps = Deps(store, ScriptedLLM([compose]), ScriptedJudge([]), RunConfig())
    with pytest.raises(ValueError, match="stale"):
        run_stage(run, "S3", deps)
    assert store.version(run) is None
    assert store.context(run).package.hypothesis.method == "New researcher method"
    store.close()
