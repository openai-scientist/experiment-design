import json

import pytest
from conftest import package
from fakes import ScriptedLLM, submit, tool_call

from experiment_design.contracts import AgentTask, TaskBudget
from experiment_design.runtime.loop import AgentSpec, SubmitRejected, Tool, run_agent
from experiment_design.runtime.store import Store


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / "exd.db")
    run = store.create_run(package(), {})
    yield store, run
    store.close()


def run(
    state,
    responses,
    handler=lambda _: {"value": 7},
    submit_handler=lambda a: a,
    allowed=("probe",),
    turns=3,
):
    store, rid = state
    task = AgentTask(
        objective="Inspect and finish",
        package_ref="math",
        design_version=None,
        allowed_tools=list(allowed),
        budget=TaskBudget(max_turns=turns, max_tokens=1000),
        exit_conditions=["Submit"],
    )
    finish = Tool("finish", "Finish", {"type": "object"}, submit_handler)
    llm = ScriptedLLM(responses)
    result = run_agent(
        AgentSpec("test", "Inspect observations"),
        task,
        "Brief",
        {"probe": Tool("probe", "Probe", {"type": "object"}, handler)},
        finish,
        llm,
        store,
        rid,
        set(),
    )
    return result, llm, store.events(rid)


def test_observation_drives_next_call(state):
    def next_response(messages, tools):
        assert json.loads(messages[-1]["content"]) == {"value": 7}
        return submit("finish", {"rationale": "Observed value 7"})

    result, _, events = run(state, [tool_call("probe", {}), next_response])
    assert result.completion_status == "completed"
    assert result.findings_or_changes["rationale"] == "Observed value 7"
    assert len([e for e in events if e.type == "tool.call"]) == 2


def test_bad_tool_args_return_error_observation(state):
    result, llm, _ = run(state, [tool_call("probe", "{not json"), submit("finish", {})])
    assert "error" in json.loads(llm.calls[1][0][-1]["content"])
    assert result.completion_status == "completed"


def test_disallowed_tool_rejected(state):
    def forbidden(_):
        pytest.fail("Disallowed handler ran")

    result, llm, events = run(
        state,
        [tool_call("probe", {}), submit("finish", {})],
        handler=forbidden,
        allowed=(),
    )
    assert "not allowed" in llm.calls[1][0][-1]["content"]
    assert [e.data["ok"] for e in events if e.type == "tool.call"] == [False, True]
    assert result.completion_status == "completed"


def test_submit_rejected_feedback(state):
    def finish(args):
        if not args.get("valid"):
            raise SubmitRejected(["paths: /x not found"])
        return args

    result, llm, _ = run(
        state,
        [submit("finish", {}), submit("finish", {"valid": True})],
        submit_handler=finish,
    )
    assert "paths: /x not found" in llm.calls[1][0][-1]["content"]
    assert result.findings_or_changes == {"valid": True}


def test_budget_exhausted_turns(state):
    result, _, events = run(state, [tool_call("probe", {}), tool_call("probe", {})], turns=2)
    assert result.completion_status == "budget_exhausted"
    assert events[-1].data["llm_calls"] == 2


def test_tool_events_have_no_full_output(state):
    result, _, events = run(
        state,
        [tool_call("probe", {}), submit("finish", {})],
        handler=lambda _: {"text": "x" * 5000},
    )
    event = [e for e in events if e.type == "tool.call"][0]
    assert len(event.data["summary"]) == 300
    assert result.completion_status == "completed"
