import json

import pytest
from conftest import design, package
from fakes import ScriptedJudge, ScriptedLLM, submit

from experiment_design.config import RunConfig
from experiment_design.contracts import ResearcherRequest, ReviewIssue
from experiment_design.runtime.llm import LLMError
from experiment_design.runtime.store import Store
from experiment_design.workflow import (
    Deps,
    WorkflowError,
    advance,
    answer_request,
    approve,
    jev_state,
    legal_actions,
    start_run,
)


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
    return submit(
        "submit_design", {"design": design().model_dump(mode="json"), "rationale": "Draft"}
    )


def review(findings=()):
    return [
        submit(
            "submit_findings", {"chain": chain(), "findings": list(findings), "summary": "Method"}
        ),
        submit("submit_findings", {"findings": [], "scenarios_checked": [], "summary": "Skeptic"}),
    ]


def revision(path="/analysis/uncertainty", value="Cluster-adjusted lower bound", addresses=("I1",)):
    return submit(
        "submit_revision",
        {
            "patch": {
                "ops": [{"path": path, "value": value}],
                "rationale": "Correct uncertainty",
                "addresses": list(addresses),
            },
            "rebuttals": [],
            "requests": [],
        },
    )


def verification(verdict="resolved"):
    return submit(
        "submit_verdicts",
        {
            "verdicts": [
                {
                    "issue_id": "I1",
                    "verdict": verdict,
                    "grounds": "Reviewed the affected design",
                    "evidence_refs": ["S1"],
                }
            ],
            "regressions": [],
        },
    )


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / "exd.db")
    yield store
    store.close()


def deps(store, responses=(), choices=(), **config):
    return Deps(store, ScriptedLLM(responses), ScriptedJudge(choices), RunConfig(**config))


def saved_run(store, **run_fields):
    run = store.create_run(package(), {})
    store.add_version(run, design(), author="design", parent=None)
    if run_fields:
        store.update_run(run, **run_fields)
    return run


def saved_issue(store, run, **fields):
    return store.save_issue(
        ReviewIssue.model_validate(
            finding() | {"run_id": run, "raised_by": "methodologist", "version_raised": 1} | fields,
        )
    )


def test_happy_path_review_revise_verify_reviewable(state):
    d = deps(
        state,
        [initial(), *review([finding()]), revision(), verification()],
        [
            {"next_action": "REVIEW"},
            {"next_action": "REVISE"},
            {"next_action": "VERIFY"},
            {"I1": "resolved"},
            {"next_action": "MARK_REVIEWABLE"},
        ],
    )
    rid = start_run(package(), d, {})
    assert advance(rid, d) == "REVIEWABLE"
    assert state.version(rid).version == 2
    assert state.issues(rid)[0].status == "resolved"
    assert any(e.type == "verification" for e in state.events(rid))


def test_legal_actions_initial(state):
    assert legal_actions(state, saved_run(state), RunConfig()) == ["REVIEW", "STOP"]


def test_unresolved_blocker_blocks_reviewable(state):
    rid = saved_run(state, reviewed_version=1, reviews_done=2)
    saved_issue(state, rid, severity="BLOCKER", status="unresolved")
    d = deps(state)
    assert "MARK_REVIEWABLE" not in legal_actions(state, rid, d.run)
    assert advance(rid, d) == "BLOCKED"


def test_single_option_skips_jev(state):
    rid = saved_run(state, reviews_done=2)
    d = deps(state)
    assert advance(rid, d) == "INCONCLUSIVE"
    assert d.judge.calls == []
    assert [e for e in state.events(rid) if e.type == "decision"][-1].data["by"] == "only_option"


def test_jev_state_is_compact(state):
    rid = saved_run(state)
    for _ in range(20):
        saved_issue(state, rid, mechanism="x" * 1000)
    snapshot = jev_state(state, rid, RunConfig())
    assert "design" not in snapshot
    assert len(json.dumps(snapshot)) < 8000


def test_patch_new_blocker_triggers_keep_repair_revert(state):
    rid = saved_run(state, reviewed_version=1)
    saved_issue(state, rid)
    d = deps(
        state,
        [revision("/sampling/cluster", None)],
        [
            {"next_action": "REVISE"},
            {"patch_outcome": "revert"},
            {"next_action": "STOP"},
        ],
    )
    assert advance(rid, d) == "INCONCLUSIVE"
    assert state.version(rid).version == 1
    assert state.issues(rid)[0].status == "open"
    assert state.run(rid).revisions_done == 1


def test_claim_change_never_committed(state):
    rid = saved_run(state, reviewed_version=1)
    saved_issue(state, rid)
    rebut = submit(
        "submit_revision",
        {
            "patch": {
                "ops": [],
                "rationale": "Evidence supports the existing design",
                "addresses": [],
            },
            "rebuttals": [
                {
                    "issue_id": "I1",
                    "argument": "Existing model handles classes",
                    "evidence_refs": ["S1"],
                }
            ],
            "requests": [],
        },
    )
    d = deps(
        state,
        [revision("/claim/margin", 5), revision("/claim/margin", 4), rebut],
        [
            {"next_action": "REVISE"},
            {"next_action": "STOP"},
        ],
    )
    advance(rid, d)
    assert state.version(rid).design.claim.margin.value == 3
    assert state.version(rid, 1).design.claim.margin.value == 3
    errors = [e for e in state.events(rid) if e.type == "tool.call" and not e.data["ok"]]
    assert len(errors) == 2 and all("researcher-owned" in e.data["summary"] for e in errors)


def test_ask_researcher_then_answer_then_resume(state):
    rid = saved_run(state)
    request = state.save_request(
        ResearcherRequest(
            run_id=rid,
            kind="decision",
            question="Which estimand?",
            path="/claim/estimand",
            raised_by="design",
        )
    )
    d = deps(state, choices=[{"next_action": "ASK_RESEARCHER"}])
    assert advance(rid, d) == "NEEDS_RESEARCHER"
    answer_request(state, rid, request.id, "ITT difference in class means")
    assert advance(rid, deps(state, choices=[{"next_action": "STOP"}])) == "INCONCLUSIVE"
    v = state.version(rid)
    assert (v.version, v.design.claim.estimand.value, v.design.claim.estimand.origin) == (
        2,
        "ITT difference in class means",
        "researcher",
    )
    assert state.requests(rid)[0].applied_in == 2


def test_numeric_answer_validated(state):
    rid = saved_run(state, status="NEEDS_RESEARCHER")
    request = state.save_request(
        ResearcherRequest(
            run_id=rid,
            kind="decision",
            question="Confirm margin",
            path="/claim/margin",
            status="open",
            raised_by="design",
        )
    )
    with pytest.raises(WorkflowError):
        answer_request(state, rid, request.id, "abc")
    assert state.requests(rid)[0].status == "open"
    answer_request(state, rid, request.id, "2.5")
    advance(rid, deps(state, choices=[{"next_action": "STOP"}]))
    assert state.version(rid).design.claim.margin.value == 2.5


def test_budget_stop(state):
    d = deps(state, [initial(), *review()], [{"next_action": "REVIEW"}], max_actions=1)
    rid = start_run(package(), d, {})
    assert advance(rid, d) == "BUDGET_STOP"
    with pytest.raises(WorkflowError):
        approve(state, rid, 1, "Researcher")


def test_resume_after_crash_mid_review(state):
    rid = saved_run(state)
    d = deps(
        state,
        [review([finding()])[0], LLMError("skeptic crashed")],
        [
            {"next_action": "REVIEW"},
        ],
    )
    with pytest.raises(LLMError):
        advance(rid, d)
    assert state.issues(rid) == [] and state.run(rid).reviews_done == 0
    assert state.events(rid)[-1].data["completion_status"] == "failed"
    resumed = deps(
        state,
        review([finding()]),
        [
            {"next_action": "REVIEW"},
            {"next_action": "STOP"},
        ],
    )
    advance(rid, resumed)
    assert state.version(rid).version == 1
    assert len(state.issues(rid)) == 1 and state.run(rid).reviews_done == 1


def test_resume_after_crash_mid_revise(state, monkeypatch):
    rid = saved_run(state, reviewed_version=1)
    saved_issue(state, rid)
    original = state.save_issue

    def crash(issue):
        if issue.status == "addressed":
            raise RuntimeError("commit interrupted")
        return original(issue)

    monkeypatch.setattr(state, "save_issue", crash)
    with pytest.raises(RuntimeError, match="commit interrupted"):
        advance(rid, deps(state, [revision()], [{"next_action": "REVISE"}]))
    assert state.version(rid).version == 1
    assert state.issues(rid)[0].status == "open"
    assert state.run(rid).revisions_done == 0
    monkeypatch.setattr(state, "save_issue", original)
    advance(
        rid,
        deps(
            state,
            [revision()],
            [
                {"next_action": "REVISE"},
                {"next_action": "STOP"},
            ],
        ),
    )
    assert state.version(rid).version == 2 and state.run(rid).revisions_done == 1


def test_verify_cannot_resolve_live_validator_issue(state):
    rid = saved_run(state, reviewed_version=1)
    bad = design()
    bad.analysis.uncertainty = None
    state.add_version(rid, bad, author="design", parent=1)
    saved_issue(
        state,
        rid,
        raised_by="validator",
        validator_code="missing_required",
        status="addressed",
        addressed_in=2,
    )
    d = deps(
        state,
        [verification()],
        [
            {"next_action": "VERIFY"},
            {"I1": "uncertain"},
            {"next_action": "STOP"},
        ],
    )
    advance(rid, d)
    verify_questions = d.judge.calls[1][1]
    assert "resolved" not in verify_questions["I1"].options
    assert state.issues(rid)[0].status == "unresolved"


def test_verifier_uncertain_maps_to_unresolved(state):
    rid = saved_run(state, reviewed_version=1)
    state.add_version(rid, design(), author="design", parent=1)
    saved_issue(state, rid, status="addressed", addressed_in=2)
    d = deps(
        state,
        [verification("uncertain")],
        [
            {"next_action": "VERIFY"},
            {"I1": "uncertain"},
            {"next_action": "MARK_REVIEWABLE"},
        ],
    )
    assert advance(rid, d) == "REVIEWABLE"
    assert state.issues(rid)[0].status == "unresolved"
