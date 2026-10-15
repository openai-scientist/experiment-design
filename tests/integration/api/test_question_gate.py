import json

import pytest

from experiment_design.api.contract_models import GateAnswer, GateKind, Phase
from experiment_design.api.runs import RequestRejected
from experiment_design.runtime.llm import LLMResponse, ToolCall
from tests.integration.api.test_run_manager import _manager, _wait
from tests.support.api_fakes import EndlessProvider, make_request


class Asking(EndlessProvider):
    def complete(self, messages, tools):
        task = json.loads(messages[1]["content"])
        if (
            "input" in task
            or not task["task"].startswith("Role: framing.")
            or self.answers
            or self.reading_calls(messages)
        ):
            return super().complete(messages, tools)
        self.answers += 1
        return LLMResponse(
            content=None,
            tokens_in=2,
            tokens_out=3,
            tool_calls=[
                ToolCall(
                    id="q",
                    name="submit",
                    arguments=json.dumps(
                        {
                            "markdown": "Brief",
                            "needs_input": True,
                            "question": "Which control?",
                            "options": ["Placebo", "Usual care", "Active control"],
                            "default": 3,
                        }
                    ),
                )
            ],
        )


def test_copilot_question_gate_and_exact_answer_resume(tmp_path):
    manager, platform, _ = _manager(tmp_path, llm_factory=lambda *a: Asking())
    run_id = manager.create(make_request(platform, stage_run_id="q", mode="copilot")).engine_run_id
    assert manager.wait_idle(run_id)
    state = manager.state(run_id)
    assert state.phase is Phase.drafting and state.waiting_input
    gate = state.open_gate
    assert gate.kind is GateKind.question
    assert [o.label for o in gate.options] == ["Placebo", "Usual care", "Active control"]
    assert gate.default_option_id == "o3"
    with pytest.raises(RequestRejected):
        manager.answer(run_id, gate.gate_id, GateAnswer(action="approve", actor="user"))
    with pytest.raises(RequestRejected) as invalid:
        manager.answer(
            run_id, gate.gate_id, GateAnswer(action="answer", actor="user", option_id="o9")
        )
    assert invalid.value.code == "GATE_OPTION_INVALID"
    assert manager.state(run_id).open_gate.gate_id == gate.gate_id
    answer = GateAnswer(
        action="answer", actor="user", decided_by="r", option_id="o2", text=" exact text "
    )
    manager.answer(run_id, gate.gate_id, answer)
    resources = _wait(manager, run_id, Phase.drafting, gate=True).open_gate
    assert resources.kind is GateKind.artifact_review and resources.step_key == "s2b_resources"
    manager.answer(
        run_id, resources.gate_id, GateAnswer(action="approve", actor="user", decided_by="r")
    )
    state = _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    assert state.open_gate.kind is GateKind.artifact_review
    assert manager.answer(run_id, gate.gate_id, answer).phase is Phase.awaiting_researcher
    record = [
        e["data"]
        for e in manager._runs[run_id].store.events()
        if e["kind"] == "researcher.response"
    ][0]
    assert (record["option"], record["option_text"], record["text"], record["researcher"]) == (
        2,
        "Usual care",
        " exact text ",
        "r",
    )


def test_light_answers_without_picking_an_option_or_opening_question_gate(tmp_path):
    manager, platform, _ = _manager(tmp_path, llm_factory=lambda *a: Asking())
    run_id = manager.create(make_request(platform, stage_run_id="q")).engine_run_id
    _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    assert manager.wait_idle(run_id) and manager.flush(run_id)
    record = [
        e["data"]
        for e in manager._runs[run_id].store.events()
        if e["kind"] == "researcher.response"
    ][0]
    assert (record["option"], record["researcher"]) == (None, "system")
    assert record["text"].startswith("No researcher can answer")
    assert not any(
        e.type == "run.gate_opened" and e.payload["kind"] == "question"
        for e in platform.events["q"]
    )
