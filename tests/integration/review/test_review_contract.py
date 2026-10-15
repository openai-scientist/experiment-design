"""Review contract v3.1 in the engine: infrastructure failures and task caps never kill a run."""

import json

import httpx
import pytest

import experiment_design.workflow.engine as engine
from experiment_design.agents.tools import run_specialist
from experiment_design.api.findings import review_findings
from experiment_design.config import LLMConfig, RunConfig
from experiment_design.contracts import AgentOutput, Finding, WorkflowError
from experiment_design.packs import validators
from experiment_design.persistence.store import RunStore
from experiment_design.publication.experiment_index import ExperimentIndex
from experiment_design.runtime.llm import LLMClient
from experiment_design.workflow.engine import advance
from tests.support.providers import Judge, Provider, Scripted

DOC = "Title line\nSecond line\nthird\n"


def store_for(tmp_path):
    source = tmp_path / "hypothesis.md"
    source.write_text("Exact hypothesis: compare two measurements.\n", encoding="utf-8")
    return RunStore.create(tmp_path / "runs", [source], {"run": RunConfig().model_dump()}, {})


def run(role, store, provider, **limits):
    return run_specialist(
        role, store, provider, RunConfig(**limits), {"doc": DOC, "other": "x\n"}, []
    )


def test_a_reviewer_over_its_turn_cap_ends_with_one_major_evidence_finding(tmp_path):
    store = store_for(tmp_path)
    turns = [[("search", {"query": "title"})]] * 5
    output = run("skeptic", store, Scripted(turns), task_max_turns=2)
    assert [(f.severity, f.kind, f.issue) for f in output.findings] == [
        ("MAJOR", "needs_evidence", "review incomplete: skeptic exceeded its turns budget")
    ]
    assert any(e["kind"] == "agent.accepted" for e in store.events())


def test_a_reviewer_over_its_token_cap_ends_with_one_major_evidence_finding(tmp_path):
    store = store_for(tmp_path)
    turns = [[("search", {"query": "title"})]] * 5  # 5 tokens a turn
    output = run("final_check", store, Scripted(turns), task_max_turns=10, task_max_tokens=8)
    assert [f.issue for f in output.findings] == [
        "review incomplete: final_check exceeded its tokens budget"
    ]
    assert (output.findings[0].severity, output.findings[0].kind) == ("MAJOR", "needs_evidence")


@pytest.mark.parametrize("limits", [{"task_max_turns": 2}, {"task_max_tokens": 8}])
def test_a_drafting_task_over_its_cap_still_stops(tmp_path, limits):
    store = store_for(tmp_path)
    turns = [[("search", {"query": "title"})]] * 5
    with pytest.raises(WorkflowError):
        run("design", store, Scripted(turns), **limits)


def test_the_run_wide_token_limit_still_stops_a_reviewer(tmp_path):
    store = store_for(tmp_path)
    store.update(llm_token_limit=4)
    with pytest.raises(WorkflowError, match="budget"):
        run("skeptic", store, Scripted([[("search", {"query": "title"})]] * 5))


def test_the_same_documents_named_again_in_one_turn_are_sent_once(tmp_path):
    store = store_for(tmp_path)
    both = ["doc", "other"]
    # Live: names plus a different name in every call returned the full set five times.
    calls = [("read", {"names": both, "name": n, "start_line": 1}) for n in (*both, "doc")]
    provider = Scripted([calls, [("submit", {"markdown": "done"})]])
    run("design", store, provider)
    first, *rest = (e["data"]["result"] for e in store.events() if e["kind"] == "tool.result")
    assert "documents" in first
    assert all(set(r) == {"repeated"} for r in rest[:2])


def test_a_503_is_zero_tokens_and_not_unknown_usage(tmp_path, monkeypatch):
    monkeypatch.setattr("experiment_design.runtime.llm.time.sleep", lambda _: None)
    store = store_for(tmp_path)
    calls = []
    submit = {"markdown": "done"}
    body = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "submit", "arguments": json.dumps(submit)},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }

    def handler(_):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json=body)

    llm = LLMClient(
        LLMConfig(base_url="https://p.example/v1", model="m"),
        "key",
        httpx.MockTransport(handler),
    )
    try:
        output = run("design", store, llm)
    finally:
        llm.close()
    assert output.markdown == "done"
    assert store.state.usage_unknown is False
    assert store.state.llm_tokens == 15


# --- R1 fix class -------------------------------------------------------------------------

EVIDENCE = {
    "id": "lic",
    "severity": "MAJOR",
    "issue": "License of the shifted dataset is unverified",
    "consequence": "Reuse terms unknown",
    "suggestion": "Look up the Zenodo record",
    "kind": "needs_evidence",
}
FIX = EVIDENCE | {"id": "ctl", "issue": "No control", "kind": "fix"}


def outputs(*, method=None, final=None):
    texts = ["Brief", "Draft", "Method review", "Skeptic review", "Final review"]
    result = [{"markdown": t} for t in texts]
    if method:
        result[2]["findings"] = [method]
    if final:
        result[4]["findings"] = [final]
    return result


def test_major_needs_evidence_alone_offers_no_revision_after_the_critics(tmp_path):
    store = store_for(tmp_path)
    cfg = RunConfig()
    evidence = AgentOutput(markdown="m", findings=[Finding.model_validate(EVIDENCE)])
    fix = AgentOutput(markdown="m", findings=[Finding.model_validate(FIX)])
    quiet = AgentOutput(markdown="s")
    options, _ = engine._critic_options(store, cfg, evidence, quiet, False)
    assert "CHECK" in options and not {"REVISE", "REFRAME"} & set(options)
    options, _ = engine._critic_options(store, cfg, fix, quiet, False)
    assert {"REVISE", "REFRAME"} <= set(options) and "CHECK" not in options
    # A researcher's change request stays authoritative.
    options, _ = engine._critic_options(store, cfg, evidence, quiet, True)
    assert options == ["REVISE"]


def test_final_check_offers_revision_only_for_a_major_fix(tmp_path):
    store = store_for(tmp_path)
    cfg = RunConfig()
    evidence = AgentOutput(markdown="f", findings=[Finding.model_validate(EVIDENCE)])
    fix = AgentOutput(markdown="f", findings=[Finding.model_validate(FIX)])
    options, forced = engine._final_options(store, cfg, evidence, [])
    assert options == ["PRESENT", "STOP"] and not forced
    options, _ = engine._final_options(store, cfg, fix, [])
    assert options == ["REVISE", "REFRAME"]  # a fixable MAJOR with refinements left is not stopped
    # A critic's maintained needs_evidence finding is no reason to revise either.
    maintained = AgentOutput(markdown="r", findings=[Finding.model_validate(EVIDENCE)])
    options, _ = engine._final_options(store, cfg, AgentOutput(markdown="f"), [maintained])
    assert options == ["PRESENT", "STOP"]


def test_stop_returns_only_when_no_refinement_is_left(tmp_path):
    store = store_for(tmp_path)
    fix = AgentOutput(markdown="f", findings=[Finding.model_validate(FIX)])
    options, forced = engine._final_options(store, RunConfig(max_refinements=0), fix, [])
    assert options == ["STOP"] and forced
    quiet = AgentOutput(markdown="s")
    options, forced = engine._critic_options(store, RunConfig(max_refinements=0), fix, quiet, False)
    assert options == ["STOP"] and forced
    options, _ = engine._critic_options(store, RunConfig(), fix, quiet, False)
    assert options == ["REVISE", "REFRAME"]


def test_a_code_check_on_the_draft_calls_for_a_revision_before_the_final_check(tmp_path):
    store = store_for(tmp_path)
    quiet = AgentOutput(markdown="q")
    seeds = Finding.model_validate(
        FIX | {"id": "seed-e1", "issue": "Fewer than three seeds declared."}
    )
    options, _ = engine._critic_options(store, RunConfig(), quiet, quiet, False, [seeds])
    assert options == ["REVISE", "REFRAME"]
    evidence = Finding.model_validate(EVIDENCE)
    options, _ = engine._critic_options(store, RunConfig(), quiet, quiet, False, [evidence])
    assert options == ["CHECK", "STOP"]


def test_a_fixable_final_finding_is_revised_not_stopped(tmp_path):
    store = store_for(tmp_path)
    texts = ["Brief", "Draft", "Method review", "Skeptic review", "Final review"]
    script = [{"markdown": t} for t in texts]
    script[4]["findings"] = [FIX]
    script += [
        {"markdown": "Revised draft"},
        {"markdown": "Method rejoinder"},
        {"markdown": "Skeptic rejoinder"},
        {
            "markdown": "Final review, clean",
            "resolutions": [{"finding_id": "P1", "status": "resolved", "reason": "Control added"}],
        },
    ]
    state = advance(store, Provider(script), Judge(), RunConfig())
    assert state.status == "PUBLISHED" and state.refinements == 1
    routes = [e["data"]["choice"] for e in store.events() if e["kind"] == "route"]
    assert routes[-2:] == ["REVISE", "PRESENT"]


def test_after_the_first_final_check_stop_is_the_judges_call_again(tmp_path):
    store = store_for(tmp_path)
    fix = AgentOutput(markdown="f", findings=[Finding.model_validate(FIX)])
    options, forced = engine._final_options(store, RunConfig(), fix, [], first_check=False)
    assert options == ["REVISE", "REFRAME", "STOP"] and not forced


def test_a_plan_with_only_major_needs_evidence_is_presented_with_its_limitations(tmp_path):
    store = store_for(tmp_path)
    state = advance(store, Provider(outputs(method=EVIDENCE, final=EVIDENCE)), Judge(), RunConfig())
    assert state.status == "PUBLISHED" and state.refinements == 0
    review = store.read(6, "researcher_review.md")
    assert "## Limitations (open, need evidence)" in review
    assert "License of the shifted dataset is unverified" in review
    assert store.read(7, "exp_plan.md") == "Draft"  # the reviewed plan bytes are unchanged


def test_the_wire_finding_carries_the_kind_and_a_lone_evidence_finding_does_not_fail_the_review(
    tmp_path,
):
    store = store_for(tmp_path)
    advance(store, Provider(outputs(final=EVIDENCE)), Judge(), RunConfig())
    wire = review_findings(store, round=1, artifact_id="a1", reviewer_model="m")
    final = [f for f in wire.findings if f.finding_id.startswith("F1-")]
    assert [(f.severity.value, f.kind.value) for f in final] == [("blocking", "needs_evidence")]
    assert wire.passed is True


def test_code_produced_resource_and_provenance_findings_are_needs_evidence():
    index = ExperimentIndex.model_validate(
        {
            "schema": "exd.experiment_index/v1",
            "plan_sha256": "x",
            "experiments": [
                {
                    "experiment_id": "E1",
                    "type": "base",
                    "hypothesis_ids": ["H1"],
                    "datasets": [],
                    "baselines": [],
                    "metrics": [],
                    "controls": [],
                    "plan_section": "## E1",
                }
            ],
        }
    )
    resources = {"resources": [{"kind": "dataset", "splits": ["test"]}]}
    [finding] = validators.project_diversity(index, resources)
    assert finding.kind == "needs_evidence"
    assert Finding.model_fields["kind"].default == "fix"
