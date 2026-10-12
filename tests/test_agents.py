import pytest

from experiment_design.agents import design as designer
from experiment_design.agents import methodologist
from experiment_design.agents.tools import ToolContext, build_tools, validate_findings
from experiment_design.contracts import Finding
from experiment_design.runtime.loop import SubmitRejected
from experiment_design.runtime.store import Store
from tests.support.builders import package
from tests.support.scenarios import plan_body, seeded_run, supported_design


@pytest.fixture
def ctx(tmp_path):
    store = Store(tmp_path / "exd.db")
    rid = seeded_run(store, package(), {})
    version = store.add_version(rid, supported_design(), author="design", parent=None)
    yield ToolContext(store, rid, package(), version, set(), task_id="agent-test")
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


def test_findings_form_rejects_bad_path(ctx):
    assert any("/nope" in e for e in validate_findings([finding(paths=["/nope"])], ctx))


def test_computation_basis_needs_tool_ref(ctx):
    assert validate_findings([finding(basis="computation")], ctx)
    observe(ctx, "abc", "compute_power")
    assert validate_findings([finding(basis="computation", evidence_refs=["tool:abc"])], ctx) == []


def test_evidence_ref_must_exist(ctx):
    assert validate_findings([finding(evidence_refs=["S9"])], ctx)
    assert validate_findings([finding()], ctx) == []
    assert validate_findings([finding(evidence_refs=["tool:abc"])], ctx)
    observe(ctx, "abc", "read_design")
    assert validate_findings([finding(evidence_refs=["tool:abc"])], ctx) == []


def test_design_initial_submit_rejects_claim_change(ctx):
    d = supported_design()
    d.claim.margin.value = 5.0
    with pytest.raises(SubmitRejected, match="researcher-owned"):
        designer.submit_tool(ctx, mode="initial").handler(
            {
                "design": plan_body(d),
                "summary": "Draft",
            }
        )


def test_designer_can_implement_bound_commitments_without_unavailable_context_tool(tmp_path):
    import json

    from experiment_design.contracts import AgentTask, FieldAnnotation, TaskBudget
    from experiment_design.runtime.loop import run_agent
    from tests.support.providers import ScriptedLLM, submit
    from tests.support.scenarios import source_context, staged, supported_design

    store = Store(tmp_path / "commitments.db")
    run, _ = staged(store)
    context = source_context()
    context.field_annotations["/analysis/model"] = FieldAnnotation(
        status="researcher-confirmed",
        source_ref="researcher:model-answer",
        value="Mixed model with class random effect.",
    )
    store.save_context(run, context)
    bound = ToolContext(store, run, context.package, None, set())
    changed = context.model_copy(deep=True)
    changed.revision += 1
    changed.field_annotations["/analysis/model"].value = "Later model choice"
    store.save_context(run, changed)
    wrong = supported_design()
    wrong.analysis.model = "Unrequested model"
    with pytest.raises(SubmitRejected) as rejected:
        designer.submit_tool(bound, mode="initial").handler(
            {"design": plan_body(wrong), "summary": "Draft"}
        )
    assert json.dumps(context.field_annotations["/analysis/model"].value) in str(rejected.value)

    def compose(messages, tools):
        data = json.loads(messages[1]["content"].split("\n")[-2])
        proposed = supported_design()
        proposed.analysis.model = data["scientific_commitments"]["/analysis/model"]
        return submit(
            "submit_design",
            {"design": plan_body(proposed), "summary": "Bound intent"},
        )

    task = AgentTask(
        id="bound-designer",
        binding=bound.binding,
        stage_id="S3",
        design_version=None,
        objective="Compose the bound initial draft",
        package_ref=context.package.id,
        allowed_tools=list(designer.TOOLS),
        budget=TaskBudget(max_turns=1, max_tokens=1000),
        exit_conditions=["Valid draft"],
    )
    result = run_agent(
        designer.SPEC,
        task,
        designer.brief(bound, mode="initial"),
        build_tools(bound),
        designer.submit_tool(bound, mode="initial"),
        ScriptedLLM([compose]),
        store,
        run,
        bound.accepted_call_ids,
    )
    assert result.completion_status == "completed"
    assert result.findings_or_changes["design"]["analysis"]["model"] == (
        "Mixed model with class random effect."
    )
    store.close()


def test_initial_submit_rejects_unattested_computation(ctx):
    d = supported_design()
    d.scenarios[0].sd.value = 1
    d.scenarios[0].sd.provenance = "computed"
    d.scenarios[0].sd.source_ref = None
    with pytest.raises(SubmitRejected, match="unattested computed quantity"):
        designer.submit_tool(ctx, mode="initial").handler(
            {"design": plan_body(d), "summary": "Draft"}
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
                "design": plan_body(supported_design()),
                "summary": "Draft",
                "decisions": [{"problem": "Which model?", "affected_paths": [path]}],
            }
        )


def test_compute_power_tool_overrides(ctx):
    tool = build_tools(ctx)["compute_power"]
    base = tool.handler({"calculator_id": "cluster_equal_size_t"})[0]
    changed = tool.handler(
        {
            "calculator_id": "cluster_equal_size_t",
            "overrides": {
                "icc": {"value": 0.2, "unit": "", "provenance": "assumption"},
            },
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
        tool.handler({"design": plan_body(supported_design())})


def test_initial_scientific_preview_remains_available(ctx):
    from tests.support.scenarios import source_context, source_snapshot, supported_design

    run = ctx.store.create_staged_run(source_snapshot(), {})
    context = ctx.store.save_context(run, source_context())
    bound = ToolContext(ctx.store, run, context.package, None, set())
    tool = build_tools(bound)["compute_power"]
    assert "design" in tool.parameters["properties"]
    assert (
        tool.handler(
            {"design": plan_body(supported_design()), "calculator_id": "cluster_equal_size_t"}
        )[0]["status"]
        == "computed"
    )


def test_rejected_reference_feedback_lists_usable_evidence(ctx):
    observe(ctx, "sensitivity-result", "read_design")
    errors = validate_findings([finding(evidence_refs=["ICC sensitivity power 0.50"])], ctx)
    feedback = " ".join(errors)
    assert "Unknown evidence ref" in feedback
    assert "tool:sensitivity-result" in feedback and "S1" in feedback


def test_researcher_input_visible_as_source(ctx):
    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import respond

    decision = ctx.store.save_decision(
        ResearcherDecision(
            id="observed-sd",
            run_id=ctx.run_id,
            binding=ctx.binding,
            status="AWAITING_RESEARCHER",
            problem="Observed SD?",
            affected_paths=["/scenarios/0/sd"],
        )
    )
    respond(
        ctx.store,
        ctx.run_id,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            researcher="PI",
            response_id="observed",
            text="Observed standard deviation is 12 points",
        ),
    )
    earlier = build_tools(ctx)["lookup_evidence"].handler({"query": "observed standard deviation"})
    assert not any(h["source_id"].startswith("researcher:observed-sd") for h in earlier)
    ctx.binding = ctx.store.binding(ctx.run_id)
    hits = build_tools(ctx)["lookup_evidence"].handler({"query": "observed standard deviation"})
    assert hits[0]["source_id"].startswith("researcher:observed-sd")


def test_read_observation_cannot_attest_a_computation(tmp_path):
    from experiment_design.contracts import Observation
    from tests.support.scenarios import decision_state

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
    from tests.support.scenarios import decision_state, supported_design

    store, run, context, _ = decision_state(tmp_path)
    bound = ToolContext(store, run, context.package, None, set(), binding=store.binding(run))
    d = supported_design()
    d.scenarios[0].sd.source_span = "Typical class size 25"
    with pytest.raises(SubmitRejected, match="source span"):
        designer.submit_tool(bound, mode="initial").handler(
            {"design": plan_body(d), "summary": "Draft"}
        )
    assert store.version(run).version == 1
    store.close()


def test_bound_power_preview_is_counterfactual_and_does_not_mutate(tmp_path):
    from tests.support.scenarios import source_context, source_snapshot, supported_design

    store = Store(tmp_path / "preview.db")
    run = store.create_staged_run(source_snapshot(), {})
    context = source_context()
    store.save_context(run, context)
    version = store.add_version(run, supported_design(), author="design", parent=None)
    bound = ToolContext(store, run, context.package, version, set(), binding=store.binding(run))
    tool = build_tools(bound)["compute_power"]
    base = tool.handler({"calculator_id": "cluster_equal_size_t"})[0]
    preview = tool.handler(
        {
            "calculator_id": "cluster_equal_size_t",
            "ops": [{"path": "/claim/margin/value", "value": 2}],
        }
    )[0]
    assert preview["counterfactual"] is True
    assert preview["power"] < base["power"]
    assert store.version(run).design_hash == version.design_hash
    assert store.context(run) == context
    store.close()


def test_bound_power_rejects_forged_source_override(tmp_path):
    from tests.support.scenarios import source_context, source_snapshot, supported_design

    store = Store(tmp_path / "override.db")
    run = store.create_staged_run(source_snapshot(), {})
    context = source_context()
    store.save_context(run, context)
    version = store.add_version(run, supported_design(), author="design", parent=None)
    bound = ToolContext(store, run, context.package, version, set(), binding=store.binding(run))
    with pytest.raises(ValueError, match="source span"):
        build_tools(bound)["compute_power"].handler(
            {
                "calculator_id": "cluster_equal_size_t",
                "overrides": {
                    "sd": {
                        "value": 100,
                        "unit": "points",
                        "provenance": "source",
                        "source_ref": "S1",
                        "source_span": "Standard deviation 10 points",
                    }
                },
            }
        )
    assert store.version(run).design_hash == version.design_hash
    store.close()


@pytest.mark.parametrize("owner", ["methodologist", "skeptic"])
def test_computation_ref_cannot_borrow_peer_observation(tmp_path, owner):
    from experiment_design.contracts import Observation
    from tests.support.scenarios import source_context, source_snapshot, supported_design

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


def test_methodologist_accepts_grounded_review_without_method_template(ctx):
    concern = finding().model_dump(mode="json")
    accepted = methodologist.submit_tool(ctx).handler(
        {
            "findings": [concern],
            "summary": "Methodological assumptions need explicit justification",
        }
    )
    assert accepted["findings"] == [concern]
    assert set(accepted) == {"findings", "summary"}


def observe(ctx, call_id, name):
    from experiment_design.contracts import Observation

    arguments = {"calculator_id": "cluster_equal_size_t"} if name == "compute_power" else {}
    result = build_tools(ctx)[name].handler(arguments)
    ctx.store.save_observation(
        ctx.run_id,
        Observation(
            id=call_id,
            task_id=ctx.task_id,
            binding=ctx.binding,
            tool_name=name,
            arguments=arguments,
            result={"value": result},
            accepted=True,
            evidence_ref=f"tool:{call_id}",
        ),
    )
    ctx.accepted_call_ids.add(call_id)
