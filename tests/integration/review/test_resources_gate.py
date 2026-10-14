import hashlib
import json

from experiment_design.api.contract_models import GateKind, Phase
from experiment_design.config import ExdConfig, LLMConfig, RunConfig
from experiment_design.contracts import Profile
from experiment_design.runtime.llm import LLMResponse, ToolCall
from experiment_design.workflow.engine import advance, respond, start_run
from tests.support.api_fakes import EndlessProvider, make_request
from tests.support.providers import Judge


def profiled_store(tmp_path):
    source = tmp_path / "hypotheses.md"
    source.write_text("## H1: First claim\n", encoding="utf-8")
    cfg = ExdConfig(
        llm=LLMConfig(base_url="http://fake", model="fake"),
        runs_root=tmp_path / "runs",
        profile=Profile(domain="ml", research_type="comparative_benchmark"),
    )
    return start_run([source], cfg), cfg.run


def design_calls(provider):
    return [
        i
        for i in range(len(provider.starts))
        if provider.task(i)["task"].startswith("Role: design.")
    ]


class AddsBaseline(EndlessProvider):
    """The selector proposes the researcher's requested baseline."""

    def complete(self, messages, tools):
        task = json.loads(messages[1]["content"])
        properties = tools[0]["function"]["parameters"]["properties"] if tools else {}
        if "input" in task and "choices" in properties:
            body = {"choices": [], "additions": [{"name": "Baseline X", "kind": "baseline"}]}
            return LLMResponse(
                content=None,
                tokens_in=2,
                tokens_out=3,
                tool_calls=[ToolCall(id="pick", name="submit", arguments=json.dumps(body))],
            )
        return super().complete(messages, tools)


def test_resources_gate_stops_before_the_designer_and_approval_continues(tmp_path):
    store, cfg = profiled_store(tmp_path)
    provider = EndlessProvider()
    state = advance(store, provider, Judge(), cfg, hold_at_review=True)
    assert (state.status, state.question_kind) == ("AWAITING_RESEARCHER", "REVIEW")
    assert design_calls(provider) == []
    assert json.loads(store.read(3, "resources.json"))["resources"] == []
    request = [e["data"] for e in store.events() if e["kind"] == "review.requested"][-1]
    assert (request["step_key"], request["name"], request["schema_id"]) == (
        "s2b_resources",
        "resources.json",
        "exd.resources/v1",
    )
    assert any(
        e["kind"] == "step.entered" and e["data"]["step_key"] == "s2b_resources"
        for e in store.events()
    )
    respond(store, action="APPROVE", text="ok", researcher="r", revision=state.attempt)
    advance(store, provider, Judge(), cfg, hold_at_review=True)
    (call,) = design_calls(provider)
    assert "resources" in provider.task(call)["required"]
    assert provider.documents(call)["resources"] == store.read(3, "resources.md").rstrip()
    manifest = json.loads((store.path / "stage-03" / "manifest.json").read_text(encoding="utf-8"))
    assert {"resources.json", "resources.md"} <= set(manifest["outputs"])


def test_request_changes_reruns_resources_and_reopens_the_gate(tmp_path):
    store, cfg = profiled_store(tmp_path)
    provider = AddsBaseline()
    state = advance(store, provider, Judge(), cfg, hold_at_review=True)
    assert "Baseline X" not in store.read(3, "resources.json")
    respond(
        store,
        action="REQUEST_CHANGES",
        text="add baseline Baseline X",
        researcher="r",
        revision=state.attempt,
    )
    state = advance(store, provider, Judge(), cfg, hold_at_review=True)
    assert (state.status, state.question_kind) == ("AWAITING_RESEARCHER", "REVIEW")
    assert "Baseline X" in store.read(3, "resources.json")
    assert design_calls(provider) == []
    requests = [e for e in store.events() if e["kind"] == "review.requested"]
    assert len(requests) == 2


def test_unprofiled_runs_skip_the_resources_step(tmp_path):
    from tests.integration.review.test_markdown_workflow import happy_outputs, store_for
    from tests.support.providers import Provider

    store = store_for(tmp_path)
    state = advance(store, Provider(happy_outputs()), Judge(), RunConfig(), hold_at_review=True)
    assert state.status == "PENDING_REVIEW"
    assert not (store.path / "stage-03" / "resources.json").exists()


def test_light_mode_approves_the_resources_gate_for_the_system(tmp_path):
    from tests.integration.api.test_run_manager import _manager, _wait

    manager, platform, _ = _manager(tmp_path, llm_factory=lambda *a: EndlessProvider())
    run_id = manager.create(make_request(platform, stage_run_id="sr1")).engine_run_id
    state = _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    assert state.open_gate.kind is GateKind.artifact_review
    assert state.open_gate.step_key == "s6_researcher_review"
    store = manager._runs[run_id].store
    approvals = [
        e["data"]
        for e in store.events()
        if e["kind"] == "researcher.response" and e["data"].get("review_of") == "s2b_resources"
    ]
    assert [(a["action"], a["researcher"]) for a in approvals] == [("APPROVE", "system")]
    assert (store.path / "stage-03" / "resources.md").is_file()


def test_publish_delivers_the_matching_experiment_index_after_the_plan(tmp_path):
    from experiment_design.api.contract_models import EXD_EXPERIMENT_INDEX, GateAnswer
    from tests.integration.api.test_run_manager import _manager, _wait

    manager, platform, _ = _manager(tmp_path, llm_factory=lambda *a: EndlessProvider())
    run_id = manager.create(make_request(platform, stage_run_id="sr1")).engine_run_id
    state = _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    answer = GateAnswer(action="approve", actor="user", decided_by="u1")
    manager.answer(run_id, state.open_gate.gate_id, answer)
    _wait(manager, run_id, Phase.published)
    manager.wait_idle(run_id)
    assert manager.flush(run_id)
    artifacts = platform.artifacts["sr1"]
    (index,) = [a for a in artifacts if a.meta.schema_id == EXD_EXPERIMENT_INDEX]
    (plan,) = [a for a in artifacts if a.meta.primary]
    assert index.meta.primary is False
    assert artifacts.index(plan) < artifacts.index(index)
    assert json.loads(index.content)["plan_sha256"] == hashlib.sha256(plan.content).hexdigest()
    assert platform.events["sr1"][-1].type == "run.published"
