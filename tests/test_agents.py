import pytest
from conftest import design, package

from experiment_design.agents import design as designer
from experiment_design.agents import methodologist
from experiment_design.agents.tools import ToolContext, build_tools, validate_findings
from experiment_design.contracts import Finding, ResearcherRequest
from experiment_design.runtime.loop import SubmitRejected
from experiment_design.runtime.store import Store


@pytest.fixture
def ctx(tmp_path):
    store = Store(tmp_path / "exd.db")
    rid = store.create_run(package(), {})
    version = store.add_version(rid, design(), author="design", parent=None)
    yield ToolContext(store, rid, package(), version, set())
    store.close()


def finding(**changes):
    return Finding(
        **(
            {
                "severity": "MAJOR",
                "paths": ["/analysis/uncertainty"],
                "mechanism": "Uncertainty ignores classes",
                "consequence": "Anti-conservative inference",
                "basis": "reasoning",
                "evidence_refs": ["S1"],
            }
            | changes
        ),
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


def test_findings_form_rejects_bad_path(ctx):
    assert any("/nope" in e for e in validate_findings([finding(paths=["/nope"])], ctx))


def test_computation_basis_needs_tool_ref(ctx):
    assert validate_findings([finding(basis="computation")], ctx)
    ctx.accepted_call_ids.add("abc")
    assert validate_findings([finding(basis="computation", evidence_refs=["tool:abc"])], ctx) == []


def test_methodologist_chain_required(ctx):
    submit = methodologist.submit_tool(ctx)
    with pytest.raises(SubmitRejected, match="metric→analysis"):
        submit.handler({"chain": chain()[:4] + chain()[5:], "findings": [], "summary": "Review"})
    broken = chain()
    broken[4]["status"] = "broken"
    with pytest.raises(SubmitRejected, match="broken"):
        submit.handler({"chain": broken, "findings": [], "summary": "Review"})


def test_evidence_ref_must_exist(ctx):
    assert validate_findings([finding(evidence_refs=["S9"])], ctx)
    assert validate_findings([finding()], ctx) == []
    assert validate_findings([finding(evidence_refs=["tool:abc"])], ctx)
    ctx.accepted_call_ids.add("abc")
    assert validate_findings([finding(evidence_refs=["tool:abc"])], ctx) == []


def test_design_initial_submit_rejects_claim_change(ctx):
    d = design()
    d.claim.margin.value = 5.0
    with pytest.raises(SubmitRejected, match="researcher-owned"):
        designer.submit_tool(ctx, mode="initial").handler(
            {
                "design": d.model_dump(mode="json"),
                "rationale": "Draft",
            }
        )


def test_initial_submit_rejects_unattested_computation(ctx):
    d = design()
    d.scenarios[0].sd.value = 1
    d.scenarios[0].sd.provenance = "computed"
    d.scenarios[0].sd.source_ref = None
    with pytest.raises(SubmitRejected, match="unattested computed quantity"):
        designer.submit_tool(ctx, mode="initial").handler(
            {"design": d.model_dump(mode="json"), "rationale": "Draft"}
        )


def test_revision_must_cover_batch(ctx):
    with pytest.raises(SubmitRejected, match="I2"):
        designer.submit_tool(ctx, mode="revise", batch=["I1", "I2"]).handler(
            {
                "patch": {"ops": [], "rationale": "Fix", "addresses": ["I1"]},
                "rebuttals": [],
                "requests": [],
            }
        )


@pytest.mark.parametrize("path", ["/analysis/model", "/claim/unknown"])
def test_decision_request_needs_claim_path(ctx, path):
    with pytest.raises(SubmitRejected, match="claim"):
        designer.submit_tool(ctx, mode="initial").handler(
            {
                "design": design().model_dump(mode="json"),
                "rationale": "Draft",
                "requests": [{"kind": "decision", "question": "Which model?", "path": path}],
            }
        )


def test_compute_power_tool_overrides(ctx):
    tool = build_tools(ctx)["compute_power"]
    base = tool.handler({})[0]
    changed = tool.handler(
        {
            "overrides": {
                "icc": {"value": 0.2, "unit": "", "provenance": "assumption"},
            }
        }
    )[0]
    assert changed["power"] < base["power"]
    assert ctx.store.version(ctx.run_id).design.sampling.cluster.icc.value == 0.10


def test_researcher_input_visible_as_source(ctx):
    ctx.store.save_request(
        ResearcherRequest(
            run_id=ctx.run_id,
            kind="input",
            question="Observed SD?",
            raised_by="design",
            status="answered",
            answer="Observed standard deviation is 12 points",
        )
    )
    hits = build_tools(ctx)["lookup_evidence"].handler({"query": "observed standard deviation"})
    assert hits[0]["source_id"] == "researcher:R1"
