import json

import pytest

from experiment_design.contracts import AgentTask, TaskBudget
from experiment_design.runtime.loop import AgentSpec, SubmitRejected, Tool, run_agent
from experiment_design.runtime.store import Store
from tests.support.builders import package
from tests.support.providers import ScriptedLLM, submit, tool_call
from tests.support.scenarios import seeded_run


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / "exd.db")
    run = seeded_run(store, package(), {})
    yield store, run
    store.close()


def run(
    state,
    responses,
    handler=lambda _: {"value": 7},
    submit_handler=lambda a: a,
    allowed=("probe",),
    turns=3,
    tokens=1000,
):
    store, rid = state
    task = AgentTask(
        objective="Inspect and finish",
        package_ref="math",
        design_version=None,
        allowed_tools=list(allowed),
        budget=TaskBudget(max_turns=turns, max_tokens=tokens),
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
        observed = json.loads(messages[-1]["content"])
        assert observed["result"] == {"value": 7}
        assert observed["evidence_ref"] == "tool:probe-result"
        return submit("finish", {"rationale": "Observed value 7"})

    result, _, events = run(state, [tool_call("probe", {}, call_id="probe-result"), next_response])
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
    assert events[-1].data["stop_reason"] == "turn_limit"
    assert events[-1].data["budget"] == {"max_turns": 2, "max_tokens": 1000}


def test_cached_input_still_exhausts_token_budget_before_tools(state):
    response = tool_call("probe", {})
    response.tokens_in = 80
    response.tokens_out = 20
    response.tokens_cached = 75

    def unexpected(_):
        pytest.fail("Tool executed after the token ceiling was reached")

    result, llm, events = run(state, [response], handler=unexpected, tokens=100)
    assert result.completion_status == "budget_exhausted"
    assert len(llm.calls) == 1
    assert not any(e.type == "tool.call" for e in events)
    assert events[-1].data["stop_reason"] == "token_limit"
    assert events[-1].data["tokens_cached"] == 75


def test_empty_task_budget_never_calls_provider(state):
    result, llm, events = run(state, [], tokens=0)
    assert result.completion_status == "budget_exhausted"
    assert llm.calls == []
    assert events[-1].data["stop_reason"] == "token_limit"


def test_cache_reporting_distinguishes_missing_from_zero(state):
    cached = tool_call("probe", {})
    cached.tokens_cached = 8
    zero = tool_call("probe", {})
    zero.tokens_cached = 0
    result, _, events = run(state, [cached, zero, submit("finish", {})])
    assert result.completion_status == "completed"
    assert events[-1].data["tokens_cached"] == 8
    assert events[-1].data["cache_usage_missing_calls"] == 1
    assert events[-1].data["stop_reason"] is None


def test_tool_events_have_no_full_output(state):
    observation = {"text": "x" * 5000 + "observation-tail"}
    result, llm, events = run(
        state,
        [tool_call("probe", {}), submit("finish", {})],
        handler=lambda _: observation,
    )
    event = [e for e in events if e.type == "tool.call"][0]
    assert len(event.data["summary"]) == 300
    assert "observation-tail" not in json.dumps([e.data for e in events])
    assert json.loads(llm.calls[1][0][-1]["content"])["result"] == observation
    assert result.completion_status == "completed"


def test_committed_submission_resumes_without_provider(tmp_path):
    from tests.support.scenarios import staged

    store = Store(tmp_path / "durable.db")
    rid, binding = staged(store)
    task = AgentTask(
        id="durable-review",
        stage_id="S4",
        binding=binding,
        objective="Review",
        package_ref="math",
        design_version=None,
        allowed_tools=["probe"],
        budget=TaskBudget(max_turns=3, max_tokens=1000),
        exit_conditions=["Submit"],
    )
    llm = ScriptedLLM(
        [
            tool_call("probe", {}, call_id="independent-probe"),
            submit("finish", {"rationale": "Independent finding"}),
        ]
    )
    args = (
        AgentSpec("test", "Inspect"),
        task,
        "Brief",
        {"probe": Tool("probe", "Probe", {"type": "object"}, lambda _: {"value": 7})},
        Tool("finish", "Finish", {"type": "object"}, lambda a: a),
        llm,
    )
    result = run_agent(*args, store, rid, set())
    tokens = store.usage(rid).llm_tokens
    store.close()
    reopened = Store(tmp_path / "durable.db")
    resumed = run_agent(*args, reopened, rid, set())
    assert resumed.findings_or_changes == result.findings_or_changes
    assert len(llm.calls) == 2
    observation = reopened.accepted_observations(task.id)[0]
    assert observation.task_id == task.id and observation.result == {"value": 7}
    assert reopened.usage(rid).llm_tokens == tokens > 0
    reopened.close()


def test_unrecorded_provider_response_retries_as_new_attempt(tmp_path):
    from tests.support.scenarios import staged

    store = Store(tmp_path / "durable.db")
    rid, binding = staged(store)
    task = AgentTask(
        id="interrupted-review",
        binding=binding,
        objective="Review",
        package_ref="math",
        design_version=None,
        allowed_tools=[],
        budget=TaskBudget(max_turns=3, max_tokens=1000),
        exit_conditions=["Submit"],
    )

    def provider_failure(messages, tools):
        raise RuntimeError("Provider disconnected")

    llm = ScriptedLLM([provider_failure, submit("finish", {"rationale": "New response"})])
    args = (
        AgentSpec("test", "Inspect"),
        task,
        "Brief",
        {},
        Tool("finish", "Finish", {"type": "object"}, lambda a: a),
        llm,
        store,
        rid,
        set(),
    )
    with pytest.raises(RuntimeError, match="disconnected"):
        run_agent(*args)
    assert store.task(task.id).submission is None
    resumed = run_agent(*args)
    assert resumed.completion_status == "completed"
    assert len(llm.calls) == 2
    assert store.task(task.id).attempt == 2
    assert any(
        e.type == "budget.stop" and e.data["reason"] == "provider_failure"
        for e in store.events(rid)
    )
    store.close()


def test_provider_call_ids_are_isolated_by_task(tmp_path):
    from tests.support.scenarios import staged

    store = Store(tmp_path / "collision.db")
    run_id, binding = staged(store)
    refs = []
    for name, value in (("method", 7), ("skeptic", 11)):
        task = AgentTask(
            id=name,
            binding=binding,
            objective="Inspect",
            package_ref="source",
            design_version=None,
            allowed_tools=["probe"],
            budget=TaskBudget(max_turns=3, max_tokens=1000),
            exit_conditions=["Submit"],
        )

        def observed(messages, tools, value=value):
            feedback = json.loads(messages[-1]["content"])
            assert feedback["result"] == {"value": value}
            refs.append(feedback["evidence_ref"])
            return submit("finish", {"evidence_refs": [feedback["evidence_ref"]]})

        result = run_agent(
            AgentSpec(name, "Inspect"),
            task,
            "Brief",
            {"probe": Tool("probe", "Probe", {}, lambda a, value=value: {"value": value})},
            Tool("finish", "Finish", {}, lambda a: a),
            ScriptedLLM([tool_call("probe", {}, "same-provider-id"), observed]),
            store,
            run_id,
            set(),
        )
        assert result.completion_status == "completed"
    assert refs[0] != refs[1]
    store.close()


def test_failed_observation_commit_returns_error_without_evidence(tmp_path):
    from tests.support.scenarios import staged

    store = Store(tmp_path / "observation.db")
    run_id, binding = staged(store)
    task = AgentTask(
        id="probe-task",
        binding=binding,
        objective="Inspect",
        package_ref="source",
        design_version=None,
        allowed_tools=["probe"],
        budget=TaskBudget(max_turns=4, max_tokens=1000),
        exit_conditions=["Submit"],
    )

    def rejected(messages, tools):
        feedback = json.loads(messages[-1]["content"])
        assert "immutable" in feedback["error"]
        assert "evidence_ref" not in feedback
        return submit("finish", {})

    result = run_agent(
        AgentSpec("review", "Inspect"),
        task,
        "Brief",
        {"probe": Tool("probe", "Probe", {}, lambda a: {"value": a["value"]})},
        Tool("finish", "Finish", {}, lambda a: a),
        ScriptedLLM(
            [
                tool_call("probe", {"value": 7}, "repeat"),
                tool_call("probe", {"value": 11}, "repeat"),
                rejected,
            ]
        ),
        store,
        run_id,
        set(),
    )
    calls = [e for e in store.events(run_id) if e.type == "tool.call"]
    assert [e.data["ok"] for e in calls] == [True, False, True]
    assert result.completion_status == "completed"
    assert len(store.accepted_observations(task.id)) == 1
    store.close()
