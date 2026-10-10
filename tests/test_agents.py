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
                "decisions": [],
            }
        )


@pytest.mark.parametrize("path", ["/analysis/unknown", "/claim/unknown"])
def test_decision_proposal_rejects_unknown_scientific_path(ctx, path):
    with pytest.raises(SubmitRejected, match="pointer"):
        designer.submit_tool(ctx, mode="initial").handler(
            {
                "design": design().model_dump(mode="json"),
                "rationale": "Draft",
                "decisions": [{"problem": "Which model?", "affected_paths": [path]}],
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


def test_review_sensitivity_schema_uses_committed_design(ctx):
    tool = build_tools(ctx)["compute_power"]
    assert "design" not in tool.parameters["properties"]
    overrides = tool.parameters["properties"]["overrides"]
    assert overrides["additionalProperties"] is False
    assert "icc" in overrides["properties"]
    assert "scenarios[0].effect" not in overrides["properties"]
    with pytest.raises(ValueError, match="committed design"):
        tool.handler({"design": design().model_dump(mode="json")})


def test_initial_scientific_preview_remains_available(ctx):
    ctx.version = None
    tool = build_tools(ctx)["compute_power"]
    assert "design" in tool.parameters["properties"]
    assert tool.handler({"design": design().model_dump(mode="json")})[0]["status"] == "computed"


def test_rejected_reference_feedback_lists_usable_evidence(ctx):
    ctx.accepted_call_ids.add("sensitivity-result")
    errors = validate_findings([finding(evidence_refs=["ICC sensitivity power 0.50"])], ctx)
    feedback = " ".join(errors)
    assert "Unknown evidence ref" in feedback
    assert "tool:sensitivity-result" in feedback and "S1" in feedback


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


def test_read_observation_cannot_attest_a_computation(tmp_path):
    from test_decisions import decision_state

    from experiment_design.contracts import Observation

    store, run, context, _ = decision_state(tmp_path)
    bound = ToolContext(
        store, run, context.package, store.version(run), {"read"}, binding=store.binding(run)
    )
    store.save_observation(
        run,
        Observation(
            id="read",
            task_id="review",
            binding=bound.binding,
            tool_name="read_design",
            arguments={},
            result={"value": "read"},
            accepted=True,
            evidence_ref="tool:read",
        ),
    )
    errors = validate_findings([finding(basis="computation", evidence_refs=["tool:read"])], bound)
    assert any("compute_power" in error for error in errors)
    store.close()


def test_source_quantity_cannot_use_unrelated_span(tmp_path):
    from test_decisions import decision_state
    from test_stage_pipeline import supported_design

    store, run, context, _ = decision_state(tmp_path)
    bound = ToolContext(store, run, context.package, None, set(), binding=store.binding(run))
    d = supported_design()
    d.scenarios[0].sd.source_span = "Typical class size 25"
    with pytest.raises(SubmitRejected, match="source span"):
        designer.submit_tool(bound, mode="initial").handler(
            {"design": d.model_dump(mode="json"), "rationale": "Draft"}
        )
    assert store.version(run).version == 1
    store.close()


def test_bound_power_preview_is_counterfactual_and_does_not_mutate(tmp_path):
    from test_stage_pipeline import source_context, source_snapshot, supported_design

    store = Store(tmp_path / "preview.db")
    run = store.create_staged_run(source_snapshot(), {})
    context = source_context()
    store.save_context(run, context)
    version = store.add_version(run, supported_design(), author="design", parent=None)
    bound = ToolContext(store, run, context.package, version, set(), binding=store.binding(run))
    tool = build_tools(bound)["compute_power"]
    base = tool.handler({})[0]
    preview = tool.handler({"ops": [{"path": "/claim/margin/value", "value": 2}]})[0]
    assert preview["counterfactual"] is True
    assert preview["power"] < base["power"]
    assert store.version(run).design_hash == version.design_hash
    assert store.context(run) == context
    store.close()


def test_bound_power_rejects_forged_source_override(tmp_path):
    from test_stage_pipeline import source_context, source_snapshot, supported_design

    store = Store(tmp_path / "override.db")
    run = store.create_staged_run(source_snapshot(), {})
    context = source_context()
    store.save_context(run, context)
    version = store.add_version(run, supported_design(), author="design", parent=None)
    bound = ToolContext(store, run, context.package, version, set(), binding=store.binding(run))
    with pytest.raises(ValueError, match="source span"):
        build_tools(bound)["compute_power"].handler(
            {
                "overrides": {
                    "sd": {
                        "value": 100,
                        "unit": "points",
                        "provenance": "source",
                        "source_ref": "S1",
                        "source_span": "Standard deviation 10 points",
                    }
                }
            }
        )
    assert store.version(run).design_hash == version.design_hash
    store.close()


@pytest.mark.parametrize("owner", ["methodologist", "skeptic"])
def test_computation_ref_cannot_borrow_peer_observation(tmp_path, owner):
    from test_stage_pipeline import source_context, source_snapshot, supported_design

    from experiment_design.contracts import Observation

    store = Store(tmp_path / "peer.db")
    run = store.create_staged_run(source_snapshot(), {})
    context = source_context()
    store.save_context(run, context)
    version = store.add_version(run, supported_design(), author="design", parent=None)
    bound = ToolContext(
        store,
        run,
        context.package,
        version,
        {"peer"},
        binding=store.binding(run),
        task_id="skeptic",
    )
    store.save_observation(
        run,
        Observation(
            id="peer",
            task_id=owner,
            binding=bound.binding,
            tool_name="compute_power",
            arguments={},
            result={"value": []},
            accepted=True,
            evidence_ref="tool:peer",
        ),
    )
    errors = validate_findings([finding(basis="computation", evidence_refs=["tool:peer"])], bound)
    assert bool(errors) == (owner != "skeptic")
    store.close()
