import json

import pytest

from experiment_design.api.contract_models import EXD_EXP_PLAN, GateAnswer, GateKind, Phase
from experiment_design.contracts import WorkflowError
from experiment_design.persistence.api_state import write_run_meta
from experiment_design.runtime.llm import LLMResponse
from experiment_design.workflow.engine import respond
from tests.integration.api.test_run_manager import FINDING, _manager, _types, _wait
from tests.support.api_fakes import EndlessProvider, make_request

MAJOR = {
    "id": "gap",
    "severity": "MAJOR",
    "issue": "No control arm",
    "consequence": "Confounding",
    "suggestion": "Add a control arm",
}


class MajorProvider(EndlessProvider):
    """Every answer of one role carries a MAJOR finding, so a run without revisions must stop."""

    def __init__(self, role, on_complete=lambda: None):
        super().__init__(on_complete)
        self.role = role

    def complete(self, messages, tools):
        response = super().complete(messages, tools)
        task = json.loads(messages[1]["content"])
        call = response.tool_calls[0] if response.tool_calls else None
        if (
            call is None
            or call.name != "submit"
            or f"Role: {self.role}." not in task.get("task", "")
        ):
            return response
        body = json.loads(call.arguments)
        call.arguments = json.dumps(body | {"findings": [MAJOR]})
        return LLMResponse(
            content=None,
            tool_calls=[call],
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
        )


def _incomplete(tmp_path, *, role="final_check", mode="light"):
    def factory(cfg, key, review_role, on_call):
        record = lambda: on_call({"role": review_role.value, "model": cfg.model, "outcome": "ok"})  # noqa: E731
        return MajorProvider(role, record)

    manager, platform, _ = _manager(tmp_path, llm_factory=factory)
    request = make_request(platform, stage_run_id="sr1", mode=mode)
    request.review_policy.max_auto_revisions = 0
    run_id = manager.create(request).engine_run_id
    return manager, platform, run_id


def test_incomplete_opens_gate(tmp_path):
    manager, platform, run_id = _incomplete(tmp_path)
    state = _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    assert manager.wait_idle(run_id) and manager.flush(run_id)
    assert state.open_gate.kind is GateKind.artifact_review and state.waiting_input
    assert manager._runs[run_id].store.state.status == "INCOMPLETE"
    (plan,) = [a for a in platform.artifacts["sr1"] if a.meta.schema_id == EXD_EXP_PLAN]
    assert plan.meta.primary is False
    assert state.open_gate.artifact_ids == [plan.artifact_id]
    findings = [a for a in platform.artifacts["sr1"] if a.meta.schema_id == "review.findings/v1"]
    assert findings and json.loads(findings[0].content)["passed"] is False
    assert "run.failed" not in _types(platform)


def test_incomplete_without_final_review_reports_the_error(tmp_path):
    manager, platform, run_id = _incomplete(tmp_path, role="methodologist")
    _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    assert manager.wait_idle(run_id) and manager.flush(run_id)
    assert not manager._runs[run_id].store.valid(5)
    done = [e for e in platform.events["sr1"] if e.type == "review.completed"]
    assert done[-1].payload["passed"] is False and done[-1].payload["error"]
    assert not [a for a in platform.artifacts["sr1"] if a.meta.schema_id == "review.findings/v1"]


@pytest.mark.parametrize("mode", ["auto", "light"])
def test_incomplete_waits_for_researcher(tmp_path, mode):
    manager, platform, run_id = _incomplete(tmp_path, mode=mode)
    gate = _wait(manager, run_id, Phase.awaiting_researcher, gate=True).open_gate
    assert manager.wait_idle(run_id)
    state = manager.state(run_id)
    assert state.phase is Phase.awaiting_researcher and state.open_gate.gate_id == gate.gate_id
    assert not [a for a in platform.artifacts["sr1"] if a.meta.primary]


def test_auto_does_not_approve_incomplete(tmp_path):
    manager, platform, run_id = _incomplete(tmp_path, mode="auto")
    _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    assert manager.wait_idle(run_id)
    store = manager._runs[run_id].store
    assert store.state.status == "INCOMPLETE" and store.state.approval != "approved"
    assert not any(
        e["kind"] == "researcher.response" and "review_of" not in e["data"] for e in store.events()
    )


def test_approve_incomplete_records_open_findings(tmp_path):
    manager, platform, run_id = _incomplete(tmp_path)
    gate = _wait(manager, run_id, Phase.awaiting_researcher, gate=True).open_gate
    manager.answer(
        run_id, gate.gate_id, GateAnswer(action="approve", actor="user", decided_by="u1")
    )
    _wait(manager, run_id, Phase.published)
    assert manager.wait_idle(run_id) and manager.flush(run_id)
    run_dir = manager._runs[run_id].store.path
    publication = json.loads(
        (run_dir / "stage-07" / "publication.json").read_text(encoding="utf-8")
    )
    assert publication["review_passed"] is False
    assert publication["scientific_status"] == "incomplete"
    assert "No control arm" in [f["issue"] for f in publication["open_findings"]]
    plan = (run_dir / "stage-07" / "exp_plan.md").read_text(encoding="utf-8")
    assert "## Finding còn mở" in plan and "No control arm" in plan
    (primary,) = [a for a in platform.artifacts["sr1"] if a.meta.primary]
    assert primary.content == plan.encode("utf-8")
    assert primary.meta.metadata["review_passed"] is False
    index = json.loads((run_dir / "stage-07" / "experiment_index.json").read_text(encoding="utf-8"))
    import hashlib

    assert index["plan_sha256"] == hashlib.sha256(plan.encode("utf-8")).hexdigest()


def test_comment_at_incomplete_runs_one_more_revision(tmp_path):
    manager, platform, run_id = _incomplete(tmp_path)
    first = _wait(manager, run_id, Phase.awaiting_researcher, gate=True).open_gate
    manager.answer(
        run_id,
        first.gate_id,
        GateAnswer(action="comment", actor="user", decided_by="u2", findings=[FINDING]),
    )
    second = _wait(manager, run_id, Phase.awaiting_researcher, gate=True).open_gate
    assert second.gate_id != first.gate_id
    store = manager._runs[run_id].store
    assert store.state.refinements == 1
    assert store.config()["run"]["max_refinements"] == 1
    assert any(e["kind"] == "revision.completed" for e in store.events())


def test_cli_incomplete_guard_unchanged(tmp_path):
    manager, platform, run_id = _incomplete(tmp_path)
    _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    assert manager.wait_idle(run_id)
    store = manager._runs[run_id].store
    write_run_meta(store.path, {})  # a CLI run has no platform_run_id
    for action in ("APPROVE", "REQUEST_CHANGES"):
        with pytest.raises(WorkflowError):
            respond(store, action=action, text="x", researcher="R", revision=store.state.attempt)
    assert store.state.status == "INCOMPLETE" and store.state.approval != "approved"


class BadFinalCheck(EndlessProvider):
    """final_check always submits a malformed finding, so the reviewer fails after two retries."""

    def complete(self, messages, tools):
        response = super().complete(messages, tools)
        task = json.loads(messages[1]["content"])
        call = response.tool_calls[0] if response.tool_calls else None
        if call and call.name == "submit" and "Role: final_check." in task.get("task", ""):
            call.arguments = json.dumps(json.loads(call.arguments) | {"findings": [{"id": "x"}]})
            return LLMResponse(content=None, tool_calls=[call], tokens_in=1, tokens_out=1)
        return response


def _review_error(tmp_path):
    def factory(cfg, key, review_role, on_call):
        record = lambda: on_call({"role": review_role.value, "model": cfg.model, "outcome": "ok"})  # noqa: E731
        return BadFinalCheck(record)

    manager, platform, _ = _manager(tmp_path, llm_factory=factory)
    run_id = manager.create(make_request(platform, stage_run_id="sr1", mode="light")).engine_run_id
    state = _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    assert manager.wait_idle(run_id)
    assert manager._runs[run_id].store.state.status == "REVIEW_ERROR"
    return manager, run_id, state.open_gate.gate_id


def test_approve_after_reviewer_failure_publishes(tmp_path):
    manager, run_id, gate_id = _review_error(tmp_path)
    state = manager.answer(
        run_id, gate_id, GateAnswer(action="approve", actor="user", decided_by="u1")
    )
    assert state.phase is Phase.published


def test_comment_after_reviewer_failure_is_accepted(tmp_path):
    manager, run_id, gate_id = _review_error(tmp_path)
    manager.answer(
        run_id,
        gate_id,
        GateAnswer(action="comment", actor="user", decided_by="u2", findings=[FINDING]),
    )
    events = manager._runs[run_id].store.events()
    actions = [e["data"]["action"] for e in events if e["kind"] == "researcher.response"]
    assert actions[-1] == "REQUEST_CHANGES"
