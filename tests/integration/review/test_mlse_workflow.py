import hashlib
import json

import pytest

from experiment_design.config import ExdConfig, LLMConfig
from experiment_design.contracts import Profile, WorkflowError
from experiment_design.publication.plan import publish
from experiment_design.workflow.engine import advance, respond, start_run
from tests.support.api_fakes import EndlessProvider
from tests.support.providers import Judge


def profiled_store(tmp_path):
    source = tmp_path / "hypotheses.md"
    source.write_text("## H1: First claim\n## H2: Second claim\n", encoding="utf-8")
    cfg = ExdConfig(
        llm=LLMConfig(base_url="http://fake", model="fake"),
        runs_root=tmp_path / "runs",
        profile=Profile(domain="ml", research_type="comparative_benchmark"),
    )
    return start_run([source], cfg), cfg.run


def reach_review(store, provider, cfg):
    state = advance(store, provider, Judge(), cfg, hold_at_review=True)
    if state.question_kind == "REVIEW":
        respond(
            store,
            action="APPROVE",
            text="Use these resources",
            researcher="r",
            revision=state.attempt,
        )
        state = advance(store, provider, Judge(), cfg, hold_at_review=True)
    return state


def test_index_deterministic_checks_and_publication_preserve_reviewed_bytes(tmp_path):
    store, cfg = profiled_store(tmp_path)
    provider = EndlessProvider()
    state = reach_review(store, provider, cfg)
    assert state.status == "PENDING_REVIEW"
    index = json.loads(store.read(3, "index.json"))
    assert index["experiments"][0]["hypothesis_ids"] == ["H1", "H2"]
    final = next(
        provider.documents(i)
        for i in range(len(provider.starts))
        if provider.task(i)["task"].startswith("Role: final_check.")
    )
    assert "deterministic_findings" in final
    reviewed = store.read(state.plan_stage, state.plan_name)
    respond(store, action="APPROVE", text="Approved", researcher="r", revision=state.attempt)
    assert store.read(7, "exp_plan.md") == reviewed
    published = json.loads(store.read(7, "experiment_index.json"))
    assert published["plan_sha256"] == hashlib.sha256(reviewed.encode()).hexdigest()


def test_index_mismatch_cannot_be_dropped_by_final_checker(tmp_path):
    from experiment_design.runtime.llm import ToolCall

    store, cfg = profiled_store(tmp_path)

    class WrongIndex(EndlessProvider):
        def complete(self, messages, tools):
            response = super().complete(messages, tools)
            if "experiments" in tools[0]["function"]["parameters"].get("properties", {}):
                body = json.loads(response.tool_calls[0].arguments)
                body["experiments"][0]["hypothesis_ids"] = ["H1"]  # H2 left without an experiment
                response = response.model_copy(
                    update={
                        "tool_calls": [
                            ToolCall(id="bad", name="submit", arguments=json.dumps(body))
                        ]
                    }
                )
            return response

    state = reach_review(store, WrongIndex(), cfg.model_copy(update={"max_refinements": 0}))
    assert state.status == "INCOMPLETE" and not state.review_complete
    findings = json.loads(store.read(5, "final_review.json"))["findings"]
    assert any(f["severity"] == "MAJOR" and f["category"] == "reproducibility" for f in findings)
    with pytest.raises(WorkflowError):
        publish(store)


def test_index_heading_and_hash_mismatches_are_repaired_by_code_not_reviewed(tmp_path):
    from experiment_design.runtime.llm import ToolCall

    store, cfg = profiled_store(tmp_path)

    class LooseIndex(EndlessProvider):
        def complete(self, messages, tools):
            response = super().complete(messages, tools)
            if "experiments" in tools[0]["function"]["parameters"].get("properties", {}):
                body = json.loads(response.tool_calls[0].arguments)
                section = body["experiments"][0]["plan_section"]
                # Live: the extracted index dropped the heading marks and the case.
                body["experiments"][0]["plan_section"] = section.lstrip("# ").upper()
                body["plan_sha256"] = "stale"
                call = ToolCall(id="loose", name="submit", arguments=json.dumps(body))
                response = response.model_copy(update={"tool_calls": [call]})
            return response

    state = reach_review(store, LooseIndex(), cfg)
    assert state.status == "PENDING_REVIEW"
    plan = store.read(state.plan_stage, state.plan_name)
    index = json.loads(store.read(state.plan_stage, "index.json"))
    assert index["experiments"][0]["plan_section"] in plan.splitlines()
    assert index["plan_sha256"] == hashlib.sha256(plan.encode()).hexdigest()
    findings = json.loads(store.read(5, "deterministic_findings.json"))
    assert not [f for f in findings if f["anchor"] == "Experiment index"]


def test_resource_shortfall_findings_need_evidence_not_a_plan_edit(tmp_path):
    store, cfg = profiled_store(tmp_path)
    reach_review(store, EndlessProvider(), cfg)
    findings = json.loads(store.read(5, "deterministic_findings.json"))
    shortfalls = [f for f in findings if f["id"].startswith("shortfall-")]
    assert shortfalls and all(f["kind"] == "needs_evidence" for f in shortfalls)
