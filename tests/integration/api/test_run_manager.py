import json
import time

import pytest

from experiment_design.api.contract_models import (
    EXD_EXP_PLAN,
    GateAnswer,
    GateKind,
    Phase,
)
from experiment_design.api.runs import (
    ConflictError,
    RequestRejected,
    RunManager,
)
from experiment_design.api.settings import ApiSettings
from experiment_design.runtime.jev import JudgeError
from experiment_design.runtime.llm import LLMError
from tests.contract.fake_platform import FakePlatform
from tests.support.api_fakes import (
    SECRET,
    TYPESAFE_SECRET,
    EndlessProvider,
    RecordingFactory,
    make_request,
)
from tests.support.providers import Judge

FINDING = {
    "finding_id": "R1",
    "anchor": {"locator": "exp_plan.md#design"},
    "severity": "major",
    "issue": "Describe the control arm",
    "author": "researcher",
}


def _manager(tmp_path, *, llm_factory=None):
    config = tmp_path / "exd.yaml"
    config.write_text(
        f"llm:\n  base_url: http://base.test/v1\n  model: base-m\nrunner: null\n"
        f"runs_root: {(tmp_path / 'runs').as_posix()}\n".replace("runner: null\n", ""),
        encoding="utf-8",
    )
    platform = FakePlatform()
    factory = RecordingFactory()
    manager = RunManager(
        ApiSettings(config_path=config, service_key=platform.service_key),
        llm_factory=llm_factory or factory,
        judge_factory=lambda cfg, key: Judge(),
        platform_transport=platform.transport(),
    )
    factory.probe = lambda: next(iter(manager._runs.values())).phase
    return manager, platform, factory


def _wait(manager, run_id, phase, *, gate=False, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = manager.state(run_id)
        if state.phase is phase and (not gate or state.open_gate is not None):
            return state
        time.sleep(0.02)
    raise AssertionError(f"timed out in {manager.state(run_id).phase}")


def _types(platform, stage_run_id="sr1"):
    return [event.type for event in platform.events.get(stage_run_id, [])]


def _start(tmp_path, **kwargs):
    manager, platform, factory = _manager(tmp_path)
    created = manager.create(make_request(platform, stage_run_id="sr1", **kwargs))
    return manager, platform, factory, created.engine_run_id


def test_light_run_reaches_review_gate(tmp_path):
    manager, platform, _, run_id = _start(tmp_path)
    state = _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    assert state.open_gate.kind is GateKind.artifact_review
    assert state.step_key == "s6_researcher_review" and state.waiting_input
    manager.wait_idle(run_id)
    assert manager.flush(run_id)
    types = _types(platform)
    assert types[:2] == ["run.phase_changed", "run.step_changed"]
    assert types.index("artifact.delivered") < types.index("run.gate_opened")
    (draft,) = [a for a in platform.artifacts["sr1"] if a.meta.schema_id == EXD_EXP_PLAN]
    assert draft.meta.primary is False
    assert state.open_gate.artifact_ids == [draft.artifact_id]
    assert [e.source_seq for e in platform.events["sr1"]] == list(
        range(1, len(platform.events["sr1"]) + 1)
    )


def test_create_is_idempotent(tmp_path):
    manager, platform, factory, run_id = _start(tmp_path)
    again = manager.create(make_request(platform, stage_run_id="sr1"))
    assert again.engine_run_id == run_id and again.created is False
    _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    assert len(factory.keys) == 2  # one stage and one review client, not four


def test_approve_publishes_primary(tmp_path):
    manager, platform, _, run_id = _start(tmp_path)
    state = _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    answer = GateAnswer(action="approve", actor="user", decided_by="u1")
    manager.answer(run_id, state.open_gate.gate_id, answer)
    final = _wait(manager, run_id, Phase.published)
    assert final.open_gate is None and not final.waiting_input
    manager.wait_idle(run_id)
    assert manager.flush(run_id)
    primary = [a for a in platform.artifacts["sr1"] if a.meta.primary]
    assert len(primary) == 1
    run_dir = manager._runs[run_id].store.path
    assert primary[0].content == (run_dir / "stage-07" / "exp_plan.md").read_bytes()
    assert primary[0].meta.metadata["scientific_status"] == "reviewed_complete"
    last = platform.events["sr1"][-1]
    assert last.type == "run.published" and last.payload == {"artifact_id": primary[0].artifact_id}
    types = _types(platform)
    assert types.index("run.gate_closed") < types.index(
        "artifact.delivered", types.index("run.gate_closed")
    )
    phase_changes = [e.phase for e in platform.events["sr1"] if e.type == "run.phase_changed"]
    assert Phase.published not in phase_changes  # Platform refuses artifacts after that
    decisions = (run_dir / "stage-06" / "decisions.jsonl").read_text(encoding="utf-8")
    assert '"researcher": "u1"' in decisions


def test_comment_goes_back_to_review_gate(tmp_path):
    manager, platform, _, run_id = _start(tmp_path)
    first = _wait(manager, run_id, Phase.awaiting_researcher, gate=True).open_gate
    manager.answer(
        run_id,
        first.gate_id,
        GateAnswer(action="comment", actor="user", decided_by="u2", findings=[FINDING]),
    )
    second = _wait(manager, run_id, Phase.awaiting_researcher, gate=True).open_gate
    assert second.gate_id != first.gate_id
    manager.wait_idle(run_id)
    assert manager.flush(run_id)
    phases = [e.phase for e in platform.events["sr1"] if e.type == "run.phase_changed"]
    after = phases[phases.index(Phase.awaiting_researcher) + 1 :]
    assert after[:2] == [Phase.revising, Phase.reviewing] and after[-1] is Phase.awaiting_researcher
    decisions = (manager._runs[run_id].store.path / "stage-06" / "decisions.jsonl").read_text(
        encoding="utf-8"
    )
    record = json.loads(decisions.splitlines()[-1])
    assert record["action"] == "REQUEST_CHANGES" and record["researcher"] == "u2"
    assert "Describe the control arm" in record["text"]


def test_system_actor_is_recorded_as_system(tmp_path):
    manager, _, _, run_id = _start(tmp_path)
    gate = _wait(manager, run_id, Phase.awaiting_researcher, gate=True).open_gate
    manager.answer(run_id, gate.gate_id, GateAnswer(action="approve", actor="system"))
    _wait(manager, run_id, Phase.published)
    log = (manager._runs[run_id].store.path / "stage-06" / "decisions.jsonl").read_text()
    assert '"researcher": "system"' in log


def test_missing_typesafe_integration_rejected(tmp_path):
    manager, platform, _ = _manager(tmp_path)
    with pytest.raises(RequestRejected, match="missing integration: typesafe") as info:
        manager.create(make_request(platform, stage_run_id="sr1", integrations={}))
    assert info.value.code == "VALIDATION_ERROR"
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


def test_unsupported_provider_rejected(tmp_path):
    manager, platform, _ = _manager(tmp_path)
    request = make_request(platform, stage_run_id="sr1")
    request.models.review.provider = "bedrock"
    with pytest.raises(RequestRejected, match="unsupported model provider"):
        manager.create(request)


def test_bad_hash_input_rejected_with_contract_code(tmp_path):
    manager, platform, _ = _manager(tmp_path)
    request = make_request(platform, stage_run_id="sr1")
    request.inputs.artifacts[0].sha256 = "0" * 64
    with pytest.raises(RequestRejected) as info:
        manager.create(request)
    assert info.value.code == "SHA256_MISMATCH"


def test_no_key_on_disk(tmp_path):
    manager, platform, _, run_id = _start(tmp_path)
    gate = _wait(manager, run_id, Phase.awaiting_researcher, gate=True).open_gate
    manager.answer(run_id, gate.gate_id, GateAnswer(action="approve", actor="user", decided_by="u"))
    _wait(manager, run_id, Phase.published)
    manager.wait_idle(run_id)
    assert manager.flush(run_id)
    leaked = [
        path
        for path in (tmp_path / "runs").rglob("*")
        if path.is_file()
        and any(secret.encode() in path.read_bytes() for secret in (SECRET, TYPESAFE_SECRET))
    ]
    assert leaked == []
    assert SECRET not in json.dumps([e.model_dump(mode="json") for e in platform.events["sr1"]])


def test_review_model_only_used_while_reviewing(tmp_path):
    manager, _, factory, run_id = _start(tmp_path, review_model="review-m")
    _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    reviewing = [model for phase, model in factory.calls if phase is Phase.reviewing]
    assert reviewing and set(reviewing) == {"review-m"}
    assert "review-m" not in {m for p, m in factory.calls if p is Phase.drafting}


def test_llm_error_fails_run(tmp_path):
    class Failing(EndlessProvider):
        def complete(self, messages, tools):
            raise LLMError("provider exploded at https://llm.test with key " + SECRET)

    manager, platform, _ = _manager(tmp_path, llm_factory=lambda *a: Failing())
    run_id = manager.create(make_request(platform, stage_run_id="sr1")).engine_run_id
    state = _wait(manager, run_id, Phase.failed)
    assert state.failure.code == "usage_unknown"  # the engine cannot account for a failed call
    assert SECRET not in state.failure.message
    manager.wait_idle(run_id)
    assert manager.flush(run_id)
    failed = [e for e in platform.events["sr1"] if e.type == "run.failed"]
    assert len(failed) == 1 and failed[0].payload["code"] == "usage_unknown"
    assert SECRET not in json.dumps(failed[0].payload)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ZeroDivisionError("boom"), "internal_error"),
        (LLMError("down at https://llm.test with key " + SECRET), "llm_error"),
        (JudgeError("down"), "judge_error"),
    ],
)
def test_escaping_exceptions_fail_the_run_instead_of_killing_the_worker(
    tmp_path, monkeypatch, error, code
):
    def explode(*args, **kwargs):
        raise error

    monkeypatch.setattr("experiment_design.api.runs.advance", explode)
    manager, platform, _ = _manager(tmp_path)
    run_id = manager.create(make_request(platform, stage_run_id="sr1")).engine_run_id
    state = _wait(manager, run_id, Phase.failed)
    assert state.failure.code == code and SECRET not in state.failure.message
    assert manager.wait_idle(run_id)
    assert manager.flush(run_id)
    failed = [e for e in platform.events["sr1"] if e.type == "run.failed"]
    assert [e.payload["code"] for e in failed] == [code]


def test_workflow_stop_is_announced_as_failure(tmp_path):
    class Silent(EndlessProvider):
        def complete(self, messages, tools):
            response = super().complete(messages, tools)
            return response.model_copy(update={"tokens_in": 10**9})

    manager, platform, _ = _manager(tmp_path, llm_factory=lambda *a: Silent())
    run_id = manager.create(make_request(platform, stage_run_id="sr1")).engine_run_id
    assert _wait(manager, run_id, Phase.failed).failure.code == "workflow_stopped"


def test_cancel_idle_run_at_gate(tmp_path):
    manager, platform, _, run_id = _start(tmp_path)
    _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    manager.wait_idle(run_id)
    state = manager.cancel(run_id)
    assert state.phase is Phase.cancelled and state.open_gate is None
    assert manager.flush(run_id)
    assert _types(platform)[-1] == "run.cancelled"
    with pytest.raises(ConflictError) as info:
        manager.cancel(run_id)
    assert info.value.code == "RUN_CLOSED"


def test_cancel_running_run_stops_the_worker(tmp_path):
    release = []

    class Gated(EndlessProvider):
        def complete(self, messages, tools):
            while not release:
                time.sleep(0.01)
            return super().complete(messages, tools)

    manager, platform, _ = _manager(tmp_path, llm_factory=lambda *a: Gated())
    run_id = manager.create(make_request(platform, stage_run_id="sr1")).engine_run_id
    deadline = time.monotonic() + 5
    while manager._runs[run_id].thread is None and time.monotonic() < deadline:
        time.sleep(0.01)
    manager._runs[run_id].store.request_interrupt("cancel")
    release.append(True)
    assert manager.wait_idle(run_id)
    assert manager.state(run_id).phase is Phase.cancelled
    assert manager._runs[run_id].store.interrupt() is None


def test_pause_then_resume_continues(tmp_path):
    release = []

    class Gated(EndlessProvider):
        def complete(self, messages, tools):
            while not release:
                time.sleep(0.01)
            return super().complete(messages, tools)

    manager, platform, _ = _manager(tmp_path, llm_factory=lambda *a: Gated())
    run_id = manager.create(make_request(platform, stage_run_id="sr1")).engine_run_id
    manager._runs[run_id].store.request_interrupt("pause")
    release.append(True)
    assert manager.wait_idle(run_id)
    assert manager.state(run_id).phase is Phase.paused
    resumed = manager.resume(run_id)
    assert resumed.phase is not Phase.paused
    final = _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    assert final.open_gate is not None


def test_pause_at_gate_keeps_gate_and_resume_restores_it(tmp_path):
    manager, _, _, run_id = _start(tmp_path)
    gate = _wait(manager, run_id, Phase.awaiting_researcher, gate=True).open_gate
    manager.wait_idle(run_id)
    paused = manager.pause(run_id)
    assert paused.phase is Phase.paused
    with pytest.raises(ConflictError):
        manager.answer(run_id, gate.gate_id, GateAnswer(action="approve", actor="user"))
    restored = manager.resume(run_id)
    assert restored.phase is Phase.awaiting_researcher
    assert restored.open_gate.gate_id == gate.gate_id
    manager.answer(run_id, gate.gate_id, GateAnswer(action="approve", actor="user", decided_by="u"))
    _wait(manager, run_id, Phase.published)


def test_unopened_and_repeated_gate_answers(tmp_path):
    manager, platform, _, run_id = _start(tmp_path)
    gate = _wait(manager, run_id, Phase.awaiting_researcher, gate=True).open_gate
    approve = GateAnswer(action="approve", actor="user", decided_by="u")
    with pytest.raises(ConflictError) as info:
        manager.answer(run_id, "g-unknown", approve)
    assert info.value.code == "GATE_NOT_OPEN"
    with pytest.raises(RequestRejected) as invalid:
        manager.answer(
            run_id, gate.gate_id, GateAnswer(action="answer", actor="user", text="go on")
        )
    assert invalid.value.code == "GATE_ACTION_INVALID"
    with pytest.raises(RequestRejected):
        manager.answer(run_id, gate.gate_id, GateAnswer(action="comment", actor="user"))
    manager.answer(run_id, gate.gate_id, approve)
    _wait(manager, run_id, Phase.published)
    manager.wait_idle(run_id)
    assert manager.flush(run_id)
    sent = len(platform.requests)
    assert manager.answer(run_id, gate.gate_id, approve).phase is Phase.published
    with pytest.raises(ConflictError) as different:
        manager.answer(
            run_id, gate.gate_id, GateAnswer(action="comment", actor="user", text="change")
        )
    assert different.value.code == "GATE_NOT_OPEN"
    assert len(platform.requests) == sent  # nothing was redone or re-sent


def test_stage_closed_by_platform_cancels_the_run(tmp_path):
    manager, platform, _ = _manager(tmp_path)
    request = make_request(platform, stage_run_id="sr1")
    platform.closed.add("sr1")
    run_id = manager.create(request).engine_run_id
    deadline = time.monotonic() + 30
    while manager.state(run_id).phase is not Phase.cancelled and time.monotonic() < deadline:
        time.sleep(0.02)
    assert manager.state(run_id).phase is Phase.cancelled
    assert platform.events.get("sr1", []) == []


def test_questions_are_answered_for_the_system_in_light_mode(tmp_path):
    asked = {"done": False}

    class Asking(EndlessProvider):
        def complete(self, messages, tools):
            if "input" in json.loads(messages[1]["content"]):
                return super().complete(messages, tools)
            calls = self.reading_calls(messages)
            if calls:
                return super().complete(messages, tools)
            self.answers += 1
            if not asked["done"] and json.loads(messages[1]["content"])["required"]:
                asked["done"] = True
                body = {
                    "markdown": "Brief",
                    "needs_input": True,
                    "question": "Which arm is the control?",
                    "options": ["Standard care", "Placebo"],
                }
            else:
                body = {"markdown": f"Output {self.answers}\n## Experiment E1: Scripted design\n"}
            from experiment_design.runtime.llm import LLMResponse, ToolCall

            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(id=f"s{self.answers}", name="submit", arguments=json.dumps(body))
                ],
                tokens_in=2,
                tokens_out=3,
            )

    manager, platform, _ = _manager(tmp_path, llm_factory=lambda *a: Asking())
    run_id = manager.create(make_request(platform, stage_run_id="sr1")).engine_run_id
    _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    log = (manager._runs[run_id].store.path / "stage-06" / "decisions.jsonl").read_text()
    record = json.loads(log.splitlines()[0])
    assert (record["action"], record["researcher"], record["option"]) == ("ANSWER", "system", 1)
