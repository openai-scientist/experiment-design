import json

import pytest

from experiment_design.config import RunConfig
from experiment_design.contracts import Design, ReviewIssue
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, advance, jev_state, legal_actions
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit, tool_call
from tests.support.scenarios import plan_body


def finding():
    return {
        "severity": "MAJOR",
        "paths": ["/analysis/uncertainty"],
        "mechanism": "Uncertainty ignores class assignment",
        "consequence": "Invalid uncertainty",
        "basis": "reasoning",
        "evidence_refs": ["S1"],
    }


def initial():
    from tests.support.scenarios import supported_design

    return submit(
        "submit_design",
        {"design": plan_body(supported_design()), "summary": "Draft"},
    )


def review(findings=()):
    return [
        submit("submit_findings", {"findings": list(findings), "summary": "Method"}),
        submit("submit_findings", {"findings": [], "scenarios_checked": [], "summary": "Skeptic"}),
    ]


def revision(
    path="/analysis/uncertainty", value="Cluster t lower confidence bound", addresses=("I1",)
):
    return submit(
        "submit_revision",
        {
            "patch": {
                "ops": [{"path": path, "value": value}],
                "rationale": "Correct uncertainty",
                "addresses": list(addresses),
            },
            "rebuttals": [],
            "decisions": [],
        },
    )


def verification(verdict="resolved"):
    def response(messages, tools):
        evidence_ref = json.loads(messages[-1]["content"])["evidence_ref"]
        return submit(
            "submit_verdicts",
            {
                "verdicts": [
                    {
                        "issue_id": "I1",
                        "verdict": verdict,
                        "grounds": "Independent calculation and actual diff inspected",
                        "evidence_refs": [evidence_ref],
                    }
                ],
                "regressions": [],
            },
        )

    return response


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / "exd.db")
    yield store
    store.close()


def deps(store, responses=(), choices=(), **config):
    return Deps(store, ScriptedLLM(responses), ScriptedJudge(choices), RunConfig(**config))


def saved_run(store, **fields):
    from tests.support.scenarios import source_context, source_snapshot, supported_design

    run = store.create_staged_run(source_snapshot(), {})
    store.save_context(
        run,
        source_context(),
    )
    store.add_version(
        run, Design.model_validate(plan_body(supported_design())), author="design", parent=None
    )
    store.update_run(run, stage_id="S4", **fields)
    return run


def saved_issue(store, run, **fields):
    return store.save_issue(
        ReviewIssue.model_validate(
            finding() | {"run_id": run, "raised_by": "methodologist", "version_raised": 1} | fields
        )
    )


def test_happy_path_review_revise_verify_reviewable(state):

    run = saved_run(state)
    d = deps(
        state,
        [
            *review([finding()]),
            revision(),
            tool_call(
                "compute_power",
                {
                    "calculator_id": "cluster_equal_size_t",
                },
                "verify-power",
            ),
            verification(),
            *review(),
        ],
        [
            {"next_action": "REVIEW"},
            {"next_action": "REVISE"},
            {"next_action": "VERIFY"},
            {"I1": "resolved"},
            {"next_action": "REVIEW"},
            {"next_action": "QUALIFY"},
        ],
    )
    assert advance(run, d) == "REVIEWABLE"
    assert state.version(run).version == 2
    assert state.issues(run)[0].status == "resolved"
    assert any(e.type == "verification" for e in state.events(run))


def test_review_budget_stop_keeps_committed_first_reviewer_private(state):
    run = saved_run(state)
    response = tool_call("read_design", {})
    response.tokens_in, response.tokens_out = 90, 10
    response.tokens_cached = 80
    d = deps(
        state, [review([finding()])[0], response], [{"next_action": "REVIEW"}], task_max_tokens=100
    )
    assert advance(run, d) == "BUDGET_STOP"
    stop = [e for e in state.events(run) if e.type == "budget.stop"][-1].data
    assert stop["scope"] == "task" and stop["agent_id"] == "skeptic"
    assert stop["tokens_used"] == stop["tokens_limit"] == 100
    assert state.issues(run) == []
    assert state.run(run).reviewed_version == 0


@pytest.mark.parametrize(
    "config,reason", [({"llm_tokens": 1}, "token_limit"), ({"max_actions": 1}, "action_limit")]
)
def test_run_budget_stop_preserves_scope(state, config, reason):
    run = saved_run(state, actions_done=1)
    state.append_event(run, "task.finished", {"tokens_in": 1, "tokens_out": 0})
    assert advance(run, deps(state, **config)) == "BUDGET_STOP"
    assert [e.data for e in state.events(run) if e.type == "budget.stop"][-1]["reason"] == reason


def test_unresolved_material_issue_cannot_qualify(state):
    run = saved_run(state, reviewed_version=1, reviews_done=2)
    saved_issue(state, run, status="unresolved")
    assert "QUALIFY" not in legal_actions(state, run, RunConfig())
    assert "REVISE" in legal_actions(state, run, RunConfig())
    assert "REVISE" not in legal_actions(state, run, RunConfig(max_revisions=0))
    assert advance(run, deps(state, choices=[{"next_action": "STOP"}])) == "INCONCLUSIVE"
    assert state.issues(run)[0].status == "unresolved"


def test_single_stop_option_skips_jev(state):
    run = saved_run(state, reviews_done=2)
    d = deps(state)
    assert advance(run, d) == "INCONCLUSIVE"
    assert d.judge.calls == []


def test_jev_state_omits_full_design_and_transcript(state):
    run = saved_run(state)
    for _ in range(20):
        saved_issue(state, run, mechanism="x" * 1000)
    payload = jev_state(state, run, RunConfig())
    assert "design" not in payload and "messages" not in payload
    assert len(json.dumps(payload)) < 8000


def test_claim_change_rejected_then_valid_repair_committed(state):
    run = saved_run(state, reviewed_version=1)
    saved_issue(state, run)
    d = deps(
        state,
        [revision("/claim/margin", 5), revision()],
        [{"next_action": "REVISE"}, {"next_action": "STOP"}],
    )
    assert advance(run, d) == "INCONCLUSIVE"
    assert state.version(run).design.claim.margin.value == 3
    errors = [e for e in state.events(run) if e.type == "tool.call" and not e.data["ok"]]
    assert len(errors) == 1 and "researcher-owned" in errors[0].data["summary"]


def test_verify_uncertain_remains_material_and_cannot_qualify(state):
    run = saved_run(state, reviewed_version=1, reviews_done=2)
    saved_issue(state, run, status="addressed", addressed_in=1)
    d = deps(
        state,
        [
            tool_call(
                "compute_power",
                {
                    "calculator_id": "cluster_equal_size_t",
                },
                "verify-power",
            ),
            verification("uncertain"),
        ],
        [{"next_action": "VERIFY"}, {"I1": "unresolved"}, {"next_action": "STOP"}],
    )
    assert advance(run, d) == "INCONCLUSIVE"
    assert state.issues(run)[0].status == "unresolved"
    assert "QUALIFY" not in legal_actions(state, run, RunConfig())
    assert "REVISE" in legal_actions(state, run, RunConfig())


def test_resume_revision_commit_failure_reuses_submission(state, monkeypatch):
    run = saved_run(state, reviewed_version=1)
    parent = state.version(run)
    saved_issue(state, run)
    original = state.save_issue

    def interrupted(issue):
        if issue.status == "addressed":
            raise RuntimeError("commit interrupted")
        return original(issue)

    monkeypatch.setattr(state, "save_issue", interrupted)
    with pytest.raises(RuntimeError, match="commit interrupted"):
        advance(run, deps(state, [revision()], [{"next_action": "REVISE"}]))
    assert state.version(run).version == 1 and state.issues(run)[0].status == "open"
    monkeypatch.setattr(state, "save_issue", original)
    resumed = deps(state, [], [{"next_action": "REVISE"}, {"next_action": "STOP"}])
    advance(run, resumed)
    assert resumed.llm.calls == []
    assert state.version(run).version == 2 and state.issues(run)[0].status == "addressed"
    assert state.version(run).parent == parent.version
    assert state.version(run, parent.version).design_hash == parent.design_hash
    assert state.version(run).design.claim == parent.design.claim
    started = [e for e in state.events(run) if e.type == "task.started" and e.agent_id == "design"]
    assert len(started) == 1
    assert state.task(started[0].task_id).task.stage_id == "S4"


def test_optional_review_preserves_independent_review_allowance(state):
    run = saved_run(state)
    d = deps(state, [], [{"next_action": "REVIEW"}], llm_tokens=100, task_max_tokens=60)
    assert advance(run, d) == "BUDGET_STOP"
    assert d.llm.calls == []
    stop = [e for e in state.events(run) if e.type == "budget.stop"][-1].data
    assert stop["reason"] == "reserved_independent_review"
    assert stop["tokens_reserved"] == 120
