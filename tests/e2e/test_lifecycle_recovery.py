import pytest

from experiment_design.config import RunConfig
from experiment_design.persistence.store import Store
from experiment_design.runtime.llm import LLMError
from experiment_design.workflow import Deps, advance, start_run
from tests.support.providers import ScriptedJudge, ScriptedLLM
from tests.support.scenarios import (
    normal_responses,
    review_responses,
    source_snapshot,
    understanding_response,
)


def test_understanding_stage_commit_failure_keeps_accepted_framing_for_resume(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "atomic.db")
    deps = Deps(store, ScriptedLLM([understanding_response()]), ScriptedJudge([]), RunConfig())
    run = start_run(source_snapshot(), deps, {})
    commit = store.commit_stage

    def interrupt(run_id, result, target):
        if result.stage_id == "S2":
            raise RuntimeError("stage commit failed")
        return commit(run_id, result, target)

    monkeypatch.setattr(store, "commit_stage", interrupt)
    with pytest.raises(RuntimeError, match="stage commit"):
        advance(run, deps)
    assert store.context(run).revision == 1
    assert store.run(run).stage_id == "S2"
    assert store.usage(run).llm_calls == 1
    monkeypatch.setattr(store, "commit_stage", commit)
    resumed = Deps(
        store,
        ScriptedLLM(normal_responses()[1:]),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    assert advance(run, resumed) == "REVIEWABLE"
    assert len(resumed.llm.calls) == 3
    store.close()


def test_resume_between_reviews_keeps_committed_first_submission(tmp_path):
    store = Store(tmp_path / "pipeline.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()[:3] + [LLMError("skeptic interrupted")]),
        ScriptedJudge([{"next_action": "REVIEW"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    with pytest.raises(LLMError):
        advance(run, deps)
    assert store.issues(run) == []
    resumed = Deps(
        store,
        ScriptedLLM([review_responses()[1], *normal_responses()[4:]]),
        ScriptedJudge([{"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    assert advance(run, resumed) == "REVIEWABLE"
    assert len(resumed.llm.calls) == 1
    started = [
        e for e in store.events(run) if e.type == "task.started" and e.agent_id == "methodologist"
    ]
    assert len(started) == 1
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
