import json

import pytest
from fakes import ScriptedJudge, ScriptedLLM, submit, tool_call

from experiment_design.config import RunConfig
from experiment_design.contracts import ReviewIssue
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, advance, jev_state, legal_actions


def chain():
    return [
        {"link": link, "status": "ok", "note": "Consistent", "evidence_refs": []}
        for link in (
            "hypothesis→estimand",
            "estimand→unit",
            "unit→sampling",
            "sampling→metric",
            "metric→analysis",
            "analysis→decision_rule",
        )
    ]


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
    from test_stage_pipeline import supported_design

    return submit(
        "submit_design",
        {"design": supported_design().model_dump(mode="json"), "rationale": "Draft"},
    )


def review(findings=()):
    return [
        submit(
            "submit_findings", {"chain": chain(), "findings": list(findings), "summary": "Method"}
        ),
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
    from test_stage_pipeline import source_context, source_snapshot, supported_design

    run = store.create_staged_run(source_snapshot(), {})
    store.save_context(
        run,
        source_context(),
    )
    store.add_version(run, supported_design(), author="design", parent=None)
    store.update_run(run, stage_id="S4", **fields)
    return run


def saved_issue(store, run, **fields):
    return store.save_issue(
        ReviewIssue.model_validate(
            finding() | {"run_id": run, "raised_by": "methodologist", "version_raised": 1} | fields
        )
    )


def test_happy_path_review_revise_verify_reviewable(state):
    from test_stage_pipeline import inspection_response

    run = saved_run(state)
    d = deps(
        state,
        [
            *review([finding()]),
            revision(),
            tool_call("compute_power", {}, "verify-power"),
            verification(),
            inspection_response(),
            submit(
                "submit_handoff",
                {"handoff_complete": True, "risks": [], "findings": [], "evidence_refs": []},
            ),
        ],
        [
            {"next_action": "REVIEW"},
            {"next_action": "REVISE"},
            {"next_action": "VERIFY"},
            {"I1": "resolved"},
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
    assert advance(run, deps(state)) == "INCONCLUSIVE"


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
        [tool_call("compute_power", {}, "verify-power"), verification("uncertain")],
        [{"next_action": "VERIFY"}, {"I1": "unresolved"}],
    )
    assert advance(run, d) == "INCONCLUSIVE"
    assert state.issues(run)[0].status == "unresolved"
    assert "QUALIFY" not in legal_actions(state, run, RunConfig())


def test_resume_revision_commit_failure_reuses_submission(state, monkeypatch):
    run = saved_run(state, reviewed_version=1)
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


def test_optional_review_preserves_verification_and_handoff_allowance(state):
    run = saved_run(state)
    d = deps(state, [], [{"next_action": "REVIEW"}], llm_tokens=100, task_max_tokens=60)
    assert advance(run, d) == "BUDGET_STOP"
    assert d.llm.calls == []
    stop = [e for e in state.events(run) if e.type == "budget.stop"][-1].data
    assert stop["reason"] == "reserved_verification_and_handoff"
    assert stop["tokens_reserved"] == 120
