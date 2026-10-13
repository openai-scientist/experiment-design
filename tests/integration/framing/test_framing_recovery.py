import json

import pytest

from experiment_design.config import RunConfig
from experiment_design.contracts import ResearcherDecision
from experiment_design.persistence.store import Store
from experiment_design.runtime.llm import LLMError
from experiment_design.workflow import Deps, advance, run_stage, start_run
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit
from tests.support.scenarios import (
    source_context,
    source_snapshot,
)


def test_two_deterministic_stages_do_not_call_provider_or_judge(tmp_path):

    store = Store(tmp_path / "deterministic.db")
    deps = Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig())
    run = start_run(source_snapshot(), deps, {})
    result = run_stage(run, "S1", deps)
    assert result.output_refs == ["source-1"]
    store.update_run(run, stage_id="S7")
    assert run_stage(run, "S7", deps).status == "NEEDS_INPUT"
    assert deps.llm.calls == [] and deps.judge.calls == []
    store.close()


def test_failed_understanding_keeps_source_and_failure_reason(tmp_path):
    store = Store(tmp_path / "failed.db")
    deps = Deps(
        store, ScriptedLLM([LLMError("provider unavailable")]), ScriptedJudge([]), RunConfig()
    )
    run = start_run(source_snapshot(), deps, {})
    with pytest.raises(LLMError, match="unavailable"):
        advance(run, deps)
    usage = store.usage(run)
    assert usage.llm_calls == 1
    assert usage.llm_usage_missing_calls == 1
    assert usage.llm_cache_usage_missing_calls == 1
    assert usage.llm_tokens == 0
    assert store.run(run).stage_id == "S2" and store.run(run).status == "FAILED"
    assert store.source_snapshot(run) == source_snapshot()
    assert any(
        e.type == "budget.stop" and e.data["reason"] == "provider_failure"
        for e in store.events(run)
    )
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
