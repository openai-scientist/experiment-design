import json

import pytest

from experiment_design.agents import explorer
from experiment_design.agents.tools import ToolContext, build_tools
from experiment_design.contracts import Observation
from experiment_design.runtime.loop import run_agent
from experiment_design.runtime.store import Store
from tests.support.exploration import exploration_payload, exploration_task, framed_run
from tests.support.providers import ScriptedLLM, submit, tool_call


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / "exd.db")
    rid = framed_run(store)
    task = store.save_exploration_task(rid, exploration_task(store, rid))
    ctx = ToolContext(store, rid, None, None, set(), binding=task.binding, task_id=task.id)
    yield store, rid, task, ctx
    store.close()


def run(state, responses):
    store, rid, task, ctx = state
    llm = ScriptedLLM(responses)
    result = run_agent(
        explorer.SPEC,
        task,
        explorer.brief(ctx),
        build_tools(ctx),
        explorer.submit_tool(ctx),
        llm,
        store,
        rid,
        ctx.accepted_call_ids,
    )
    return result, llm


def test_partial_hypothesis_reaches_structured_submit(state):
    result, llm = run(
        state, [tool_call("read_sources", {}), submit("submit_exploration", exploration_payload())]
    )
    assert result.completion_status == "completed"
    assert result.findings_or_changes["candidates"][0]["id"] == "probe-a"
    source_feedback = json.loads(llm.calls[1][0][-1]["content"])
    assert source_feedback["result"]["artifacts"][0]["ref"] == "S1"
    assert state[0].task(state[2].id).submission == result


def test_unknown_ref_feedback_allows_corrected_submit(state):
    bad = exploration_payload()
    bad["priorities"][0]["evidence_refs"] = ["made-up"]
    result, llm = run(
        state,
        [submit("submit_exploration", bad), submit("submit_exploration", exploration_payload())],
    )
    assert result.completion_status == "completed"
    feedback = json.loads(llm.calls[1][0][-1]["content"])
    assert "Unknown evidence ref made-up" in str(feedback["error"])
    assert "S1" in str(feedback["error"])


def test_peer_observation_cannot_support_candidate(state):
    store, rid, task, ctx = state
    store.save_observation(
        rid,
        Observation(
            id="peer:read",
            task_id="peer",
            binding=task.binding,
            tool_name="read_sources",
            arguments={},
            result={},
            accepted=True,
            evidence_ref="tool:peer:read",
        ),
    )
    ctx.accepted_call_ids.add("peer:read")
    bad = exploration_payload()
    bad["candidates"][0]["evidence_refs"] = ["tool:peer:read"]
    result, llm = run(
        state,
        [submit("submit_exploration", bad), submit("submit_exploration", exploration_payload())],
    )
    assert "Unknown evidence ref" in llm.calls[1][0][-1]["content"]
    assert result.findings_or_changes["candidates"][0]["evidence_refs"] == []


def test_explorer_cannot_call_design_or_power_tools(state):
    result, llm = run(
        state,
        [
            tool_call(
                "compute_power",
                {
                    "calculator_id": "cluster_equal_size_t",
                },
            ),
            submit("submit_exploration", exploration_payload()),
        ],
    )
    assert "not allowed" in llm.calls[1][0][-1]["content"]
    offered = {tool["function"]["name"] for tool in llm.calls[0][1]}
    assert offered == {"read_sources", "lookup_evidence", "submit_exploration"}
    assert result.completion_status == "completed"
    assert state[0].version(state[1]) is None
