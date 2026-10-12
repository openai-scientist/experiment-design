import subprocess
import sys
from pathlib import Path

import pytest

from experiment_design.config import RunConfig
from experiment_design.runtime.jev import Decision, JudgeError
from experiment_design.runtime.llm import LLMError
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, advance
from tests.support.exploration import (
    advance_framing,
    exploration_payload,
    exploration_task,
    framed_run,
)
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit
from tests.support.scenarios import source_snapshot


def setup(tmp_path, case="useful_probe", cfg=None, responses=None, judge=None):
    store = Store(tmp_path / "exd.db")
    llm = ScriptedLLM(
        responses
        if responses is not None
        else [submit("submit_exploration", exploration_payload(case))]
    )
    deps = Deps(store, llm, judge or ScriptedJudge([]), cfg or RunConfig())
    rid = framed_run(store)
    store.update_run(rid, config={"run": deps.run.model_dump()})
    return store, deps, rid


def test_explore_freezes_recommendation_without_design(tmp_path):
    store, deps, rid = setup(tmp_path)
    assert advance_framing(rid, deps) == "RUNNING"
    snapshot = store.exploration_snapshot(rid)
    assert snapshot.selection.choice == "recommend:probe-a"
    assert store.version(rid) is None
    assert store.approvals(rid) == []
    assert store.run(rid).stage_id == "S3"
    assert store.exploration_snapshot(rid) == snapshot
    assert len(deps.llm.calls) == 1
    assert store.run(rid).actions_done == 2
    store.close()


def test_unavailable_candidate_is_not_a_legal_next_step(tmp_path):
    store, deps, rid = setup(tmp_path, "unavailable_first_tier")
    assert advance_framing(rid, deps) == "RUNNING"
    assert store.exploration_snapshot(rid).selection.choice == "recommend:probe-b"
    assert deps.judge.calls == []
    store.close()


def test_tied_conditional_options_keep_limits(tmp_path):
    judge = ScriptedJudge([{"next_probe": "recommend:probe-a"}])
    store, deps, rid = setup(tmp_path, "conditional_tie", judge=judge)
    assert advance_framing(rid, deps) == "RUNNING"
    assert set(judge.calls[0][1]["next_probe"].options) == {
        "recommend:probe-a",
        "recommend:probe-b",
    }
    assert judge.calls[0][0]["sources"] == source_snapshot().model_dump(mode="json")
    assert judge.calls[0][0]["observations"] == []
    candidate = store.exploration_snapshot(rid).submission.candidates[0]
    assert candidate.conditions == ["Sample access is confirmed"]
    store.close()


def test_defer_without_candidates_preserves_unknowns(tmp_path):
    store, deps, rid = setup(tmp_path, "missing_input")
    assert advance_framing(rid, deps) == "NEEDS_RESEARCHER"
    snapshot = store.exploration_snapshot(rid)
    assert snapshot.submission.unknowns == ["Sample availability"]
    assert snapshot.selection.choice == "DEFER"
    assert snapshot.outcome == "needs_input"
    assert advance(rid, deps) == "NEEDS_RESEARCHER"
    assert len(deps.llm.calls) == 1
    store.close()


def test_resume_after_submission_does_not_regenerate(tmp_path, monkeypatch):
    judge = ScriptedJudge([{"next_probe": "recommend:probe-b"}])
    store, deps, rid = setup(tmp_path, "conditional_tie", judge=judge)
    save = store.save_exploration_selection
    monkeypatch.setattr(
        store, "save_exploration_selection", lambda *_: (_ for _ in ()).throw(RuntimeError("crash"))
    )
    with pytest.raises(RuntimeError, match="crash"):
        advance_framing(rid, deps)
    assert store.task(exploration_task(store, rid).id).submission is not None
    monkeypatch.setattr(store, "save_exploration_selection", save)
    deps.judge = ScriptedJudge([{"next_probe": "recommend:probe-b"}])
    assert advance_framing(rid, deps) == "RUNNING"
    assert len(deps.llm.calls) == 1
    store.close()


def test_resume_after_selection_does_not_rejudge(tmp_path, monkeypatch):
    judge = ScriptedJudge([{"next_probe": "recommend:probe-a"}])
    store, deps, rid = setup(tmp_path, "conditional_tie", judge=judge)
    freeze = store.freeze_exploration
    monkeypatch.setattr(
        store, "freeze_exploration", lambda *_: (_ for _ in ()).throw(RuntimeError("crash"))
    )
    with pytest.raises(RuntimeError, match="crash"):
        advance_framing(rid, deps)
    assert store.exploration_selection(rid).choice == "recommend:probe-a"
    monkeypatch.setattr(store, "freeze_exploration", freeze)
    assert advance_framing(rid, deps) == "RUNNING"
    assert len(judge.calls) == 1
    assert len(deps.llm.calls) == 1
    store.close()


def test_hostile_judge_choice_is_rejected_before_selection(tmp_path):
    class HostileJudge:
        def decide(self, state, questions):
            return {"next_probe": Decision("recommend:foreign", {}, 1.0)}

    store, deps, rid = setup(tmp_path, "conditional_tie", judge=HostileJudge())
    with pytest.raises(JudgeError, match="illegal"):
        advance_framing(rid, deps)
    assert store.exploration_selection(rid) is None
    assert store.exploration_snapshot(rid) is None
    assert store.run(rid).stage_id == "S2"
    store.close()


def test_interrupted_task_resumes_with_cumulative_unknown_usage(tmp_path):
    store, deps, rid = setup(
        tmp_path,
        responses=[LLMError("offline"), submit("submit_exploration", exploration_payload())],
    )
    with pytest.raises(LLMError):
        advance_framing(rid, deps)
    task = store.task(exploration_task(store, rid).id)
    assert task.status == "interrupted"
    assert store.run(rid).stage_id == "S2"
    assert store.usage(rid).llm_usage_missing_calls == 1
    assert advance_framing(rid, deps) == "RUNNING"
    assert store.task(task.task.id).task.budget == task.task.budget
    assert store.usage(rid).llm_calls == 2
    assert store.run(rid).actions_done == 2
    store.close()


@pytest.mark.parametrize("limit,expected", [(15, "BUDGET_STOP"), (16, "RUNNING")])
def test_token_boundary_preserves_actual_runtime_result(tmp_path, limit, expected):
    store, deps, rid = setup(tmp_path, cfg=RunConfig(llm_tokens=limit))
    assert advance_framing(rid, deps) == expected
    assert store.usage(rid).llm_tokens == 15
    if expected == "BUDGET_STOP":
        calls = len(deps.llm.calls)
        assert advance_framing(rid, deps) == "BUDGET_STOP"
        assert len(deps.llm.calls) == calls
    store.close()


def test_reserve_actions_before_provider_and_allow_last_slot_freeze(tmp_path):
    store, deps, rid = setup(tmp_path, cfg=RunConfig(max_actions=1))
    assert advance_framing(rid, deps) == "BUDGET_STOP"
    assert deps.llm.calls == []
    config = store.run(rid).config | {"run": RunConfig(max_actions=2).model_dump()}
    store.update_run(rid, config=config)
    assert advance_framing(rid, deps) == "RUNNING"
    assert store.run(rid).actions_done == 2
    store.close()


def test_over_budget_jev_usage_preserves_selection_until_authorized_increase(tmp_path):
    class CostlyJudge:
        calls = 0

        def decide(self, state, questions):
            self.calls += 1
            return {"next_probe": Decision("recommend:probe-a", {}, 1.0, tokens=30)}

    judge = CostlyJudge()
    store, deps, rid = setup(tmp_path, "conditional_tie", cfg=RunConfig(llm_tokens=40), judge=judge)
    assert advance_framing(rid, deps) == "BUDGET_STOP"
    assert store.exploration_selection(rid) is not None
    assert store.exploration_snapshot(rid) is None
    store.update_run(rid, config={"run": RunConfig(llm_tokens=60).model_dump()})
    assert advance_framing(rid, deps) == "RUNNING"
    assert judge.calls == 1
    assert store.usage(rid).llm_tokens == 45
    store.close()


@pytest.mark.parametrize("turns,tokens", [(1, 100), (5, 20)])
def test_hard_exit_after_usage_cannot_reset_pinned_task_budget(tmp_path, turns, tokens):
    store, deps, rid = setup(tmp_path, cfg=RunConfig(task_max_turns=turns, task_max_tokens=tokens))
    script = """
import os, sys
sys.path.insert(0, sys.argv[3])
from tests.support.exploration import exploration_payload
from tests.support.providers import ScriptedLLM, ScriptedJudge, submit
from experiment_design.config import RunConfig
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps
from tests.support.exploration import advance_framing
store = Store(sys.argv[1])
append = store.append_event
def crash(run_id, type, data, **meta):
    event = append(run_id, type, data, **meta)
    if type == 'provider.usage' and meta.get('task_id'):
        os._exit(73)
    return event
store.append_event = crash
deps = Deps(store, ScriptedLLM([submit('submit_exploration', exploration_payload())]),
            ScriptedJudge([]), RunConfig())
advance_framing(sys.argv[2], deps)
"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(tmp_path / "exd.db"),
            rid,
            str(Path(__file__).parent),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 73, process.stderr
    assert store.usage(rid).llm_tokens == 15
    assert store.task(exploration_task(store, rid).id).usage == {}
    assert advance_framing(rid, deps) == "BUDGET_STOP"
    assert store.exploration_snapshot(rid) is None
    assert store.task(exploration_task(store, rid).id).task.budget.max_tokens == tokens
    if turns == 1:
        assert deps.llm.calls == []
        assert store.usage(rid).llm_calls == 1
    else:
        assert store.usage(rid).llm_tokens == 30
        assert store.task(exploration_task(store, rid).id).usage["tokens_in"] == 20
    store.close()


def test_concurrent_advance_is_rejected_before_provider_and_checkpoint(tmp_path):
    store, deps, rid = setup(tmp_path)
    other = Store(tmp_path / "exd.db")
    peer_llm = ScriptedLLM([submit("submit_exploration", exploration_payload("missing_input"))])
    peer = Deps(other, peer_llm, ScriptedJudge([]), RunConfig())

    def interleaved(messages, tools):
        with pytest.raises(ValueError, match="already|active|concurrent"):
            advance_framing(rid, peer)
        return submit("submit_exploration", exploration_payload())

    deps.llm = ScriptedLLM([interleaved])
    assert advance_framing(rid, deps) == "RUNNING"
    assert peer_llm.calls == []
    frozen = store.exploration_snapshot(rid)
    assert (
        frozen.submission.model_dump(mode="json")
        == store.task(frozen.task_id).submission.findings_or_changes
    )
    assert store.usage(rid).llm_calls == 1
    other.close()
    store.close()
