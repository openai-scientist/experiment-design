import pytest

from experiment_design.contracts import DecisionResponse, ResearcherDecision
from experiment_design.design.approval import can_approve
from experiment_design.workflow.qualification import approve_version, assess_readiness
from tests.support.scenarios import decision_state


def test_unreviewed_plan_cannot_approve(tmp_path):
    store, run, context, decision = decision_state(tmp_path)
    assessment = assess_readiness(store, run)
    assert not assessment.plan_complete
    assert can_approve(store.publication_snapshot(run))
    store.update_run(run, stage_id="S6")
    final = store.save_decision(
        ResearcherDecision(
            id="final",
            run_id=run,
            purpose="final_review",
            status="AWAITING_RESEARCHER",
            binding=store.binding(run),
            problem="Final review",
        )
    )
    response = DecisionResponse(
        revision=1, action="APPROVE_VERSION", researcher="researcher", response_id="approve-1"
    )
    with pytest.raises(ValueError, match="approval"):
        approve_version(store, run, final.id, response)
    assert store.approvals(run) == []
    store.close()


@pytest.mark.parametrize("complete", [True, False])
def test_open_method_plan_requires_concrete_scientific_procedure(complete, tmp_path):
    from experiment_design.contracts import Design, Hypothesis, ScientificPackage
    from experiment_design.science.validators import validate_design

    package = ScientificPackage(
        id="qualitative",
        research_question="What explains interview variation?",
        hypothesis=Hypothesis(statement="Context explains reported variation"),
    )
    plan = Design.model_validate(
        {
            "claim": {"hypothesis": {"value": package.hypothesis.statement, "origin": "package"}},
            "rationale": [
                {
                    "choice": "Comparative thematic analysis",
                    "why": "Examine accounts across contexts",
                }
            ],
            "measurement": {
                "primary_metric": {
                    "name": "coded themes",
                    "kind": "qualitative",
                    "unit": "theme",
                    "operationalization": "Apply the stated coding rubric to transcript passages",
                },
                "data_collection": "Supplied anonymized transcripts",
            },
            "analysis": {
                "model": "Comparative thematic analysis",
                "uncertainty": "Disclose discrepant cases",
                "decision_rule": "Retain explanations supported across contexts",
                "stopping": "All supplied transcripts",
            },
            "protocol": {
                "steps": ["Code anonymized passages", "Compare cases and report discrepancies"]
                if complete
                else [],
                "stop_conditions": ["Stop if anonymity cannot be maintained"],
                "resources": ["Supplied transcripts"],
                "feasibility": "Within the supplied material and researcher constraints",
            },
            "execution": {
                "purpose": "Compare proposed explanations",
                "inputs": ["Anonymized transcripts"],
                "fixed_scientific_choices": ["Coding rubric and cross-context comparison"],
                "expected_outputs": [
                    {
                        "name": "theme report",
                        "description": "Themes and discrepant cases",
                        "format": "Markdown",
                        "validation": "Cite supporting passages",
                    }
                ],
            },
        }
    )
    findings = validate_design(plan, package)
    material = [f for f in findings if f.severity in {"MAJOR", "BLOCKER"}]
    assert bool(material) is not complete
    if not complete:
        assert any(f.path == "/protocol/steps" for f in material)
    assert plan.analysis.alpha is None and plan.analysis.sidedness is None
    assert plan.conditions == [] and plan.scenarios == []
    if complete:
        from experiment_design.config import RunConfig
        from experiment_design.contracts import FieldAnnotation, ResearchContext
        from experiment_design.design.intake import snapshot_sources
        from experiment_design.persistence.store import Store
        from experiment_design.workflow import Deps, advance, start_run
        from tests.support.providers import ScriptedJudge, ScriptedLLM
        from tests.support.scenarios import review_responses

        path = tmp_path / "qualitative.md"
        path.write_bytes(package.hypothesis.statement.encode("utf-8"))
        source = snapshot_sources([path])
        store = Store(tmp_path / "qualitative.db")
        deps = Deps(
            store,
            ScriptedLLM(review_responses()),
            ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
            RunConfig(),
        )
        run = start_run(source, deps, {})
        store.save_context(
            run,
            ResearchContext(
                id="qualitative",
                revision=1,
                source_snapshot_id=source.id,
                package=package,
                field_annotations={
                    "hypothesis": FieldAnnotation(
                        status="source-stated",
                        source_ref=source.artifacts[0].ref,
                        source_span=package.hypothesis.statement,
                    )
                },
            ),
        )
        store.add_version(run, plan, author="design", parent=None)
        store.update_run(run, stage_id="S4")
        assert advance(run, deps) == "REVIEWABLE"
        assert store.assessment(run).plan_complete
        assert store.publication_snapshot(run).calculations == []
        assert len(deps.llm.calls) == 2
        store.close()


def test_supported_exact_version_approves_and_new_context_invalidates(tmp_path):
    from experiment_design.contracts import CalculationRequest, Observation
    from experiment_design.science.power import calculate

    store, run, context, decision = decision_state(tmp_path)
    # This case has no pending scientific decision.
    decision.status = "APPLIED"
    decision.verification = {"status": "verified"}
    store.save_decision(decision)
    d = store.version(run).design
    d.analysis.method_id = "cluster_equal_size_t"
    d.analysis.model = "Pooled t test of cluster means"
    d.analysis.common_variance = True
    d.sampling.unit_of_analysis = "class"
    d.sampling.cluster.equal_size = d.sampling.cluster.independent_clusters = True
    d.sampling.cluster.clusters_per_arm = 25
    store.add_version(run, d, author="design", parent=1)
    complete_review(store, run)
    binding = store.binding(run)
    result = calculate(
        CalculationRequest(
            calculator_id=(d).analysis.method_id, binding=binding, scenario_id="base", design=d
        )
    )
    store.save_observation(
        run,
        Observation(
            id="qualification-power",
            task_id="qualification",
            binding=binding,
            tool_name="compute_power",
            arguments={},
            result={"value": [result.model_dump(mode="json")]},
            accepted=True,
            evidence_ref="tool:qualification-power",
        ),
    )
    assessment = assess_readiness(store, run)
    assert assessment.review_status == "REVIEWABLE"
    store.update_run(run, stage_id="S6")
    final = store.save_decision(
        ResearcherDecision(
            id="final",
            run_id=run,
            purpose="final_review",
            status="AWAITING_RESEARCHER",
            binding=store.binding(run),
            problem="Final review",
        )
    )
    approval = approve_version(
        store,
        run,
        final.id,
        DecisionResponse(
            revision=1, action="APPROVE_VERSION", researcher="researcher", response_id="approve-1"
        ),
    )
    assert approval.design_hash == store.version(run).design_hash
    assert not can_approve(store.publication_snapshot(run))
    store.save_context(
        run, context.model_copy(update={"revision": 2, "unknowns": ["New researcher evidence"]})
    )
    assert can_approve(store.publication_snapshot(run))
    assert store.publication_snapshot(run).approval is None
    store.close()


def test_supported_but_underpowered_recruitment_records_scientific_blocker(tmp_path):
    from experiment_design.contracts import CalculationRequest, Observation
    from experiment_design.science.power import calculate

    store, run, context, decision = decision_state(tmp_path)
    decision.status = "APPLIED"
    decision.verification = {"status": "verified"}
    store.save_decision(decision)
    from tests.support.scenarios import supported_design

    d = supported_design()
    d.sampling.cluster.clusters_per_arm = 10
    store.add_version(run, d, author="design", parent=1)
    complete_review(store, run)
    binding = store.binding(run)
    calculation = calculate(
        CalculationRequest(
            calculator_id=(d).analysis.method_id, binding=binding, scenario_id="base", design=d
        )
    )
    store.save_observation(
        run,
        Observation(
            id="too-small",
            task_id="handoff",
            binding=binding,
            tool_name="compute_power",
            arguments={},
            result={"value": [calculation.model_dump(mode="json")]},
            accepted=True,
            evidence_ref="tool:too-small",
        ),
    )
    assessment = assess_readiness(store, run)
    assert assessment.review_status == "REVIEWABLE"
    assert not assessment.plan_complete
    assert can_approve(store.publication_snapshot(run))
    assert any("target power" in reason for reason in assessment.blockers)
    store.close()


def test_new_material_issue_invalidates_assessment_without_design_change(tmp_path):
    from experiment_design.contracts import CalculationRequest, Observation, ReviewIssue
    from experiment_design.persistence.store import Store
    from experiment_design.science.power import calculate
    from tests.support.scenarios import source_context, source_snapshot, supported_design

    store = Store(tmp_path / "material-issue.db")
    try:
        run = store.create_staged_run(source_snapshot(), {})
        store.save_context(run, source_context())
        version = store.add_version(run, supported_design(), author="design", parent=None)
        complete_review(store, run)
        version = store.version(run)
        calculation = calculate(
            CalculationRequest(
                calculator_id=(version.design).analysis.method_id,
                binding=store.binding(run),
                scenario_id="base",
                design=version.design,
            )
        )
        store.save_observation(
            run,
            Observation(
                id="ready-power",
                task_id="handoff",
                binding=store.binding(run),
                tool_name="compute_power",
                arguments={},
                result={"value": [calculation.model_dump(mode="json")]},
                accepted=True,
                evidence_ref="tool:ready-power",
            ),
        )
        assessment = assess_readiness(store, run)
        assert assessment.plan_complete is True
        assert can_approve(store.publication_snapshot(run)) == []
        events = store.events(run)
        unchanged = store.publication_snapshot(run)
        assert unchanged.assessment == assessment
        assert can_approve(unchanged) == []
        assert store.events(run) == events
        store.save_observation(
            run,
            Observation(
                id="later-evidence",
                task_id="independent-review",
                binding=store.binding(run),
                tool_name="lookup_evidence",
                arguments={"query": "new evidence"},
                result={"value": []},
                accepted=True,
                evidence_ref="tool:later-evidence",
            ),
        )
        assert store.publication_snapshot(run).assessment is None
        assessment = assess_readiness(store, run)
        assert assessment.plan_complete

        store.save_issue(
            ReviewIssue(
                run_id=run,
                raised_by="handoff",
                version_raised=version.version,
                severity="MAJOR",
                paths=["/measurement/data_collection"],
                mechanism="Data collection authorization needs confirmation",
                consequence="Collection cannot proceed until the new concern is reconciled",
                basis="unknown_assumption",
            )
        )
        changed = store.publication_snapshot(run)
        assert changed.version.version == version.version
        assert changed.version.design_hash == version.design_hash
        assert changed.assessment is None
        assert "approval requires current REVIEWABLE and complete reviewed plan" in can_approve(
            changed
        )
        assert "material issues remain unresolved" in can_approve(changed)
    finally:
        store.close()


def complete_review(store, run):
    from experiment_design.config import RunConfig
    from experiment_design.contracts import Design
    from experiment_design.workflow import Deps, run_stage
    from tests.support.providers import ScriptedJudge, ScriptedLLM
    from tests.support.scenarios import plan_body, review_responses

    current = store.version(run)
    if current.design.execution is None:
        store.add_version(
            run,
            Design.model_validate(plan_body(current.design)),
            author="design",
            parent=current.version,
        )
    store.update_run(run, stage_id="S4")
    run_stage(
        run,
        "S4",
        Deps(
            store,
            ScriptedLLM(review_responses()),
            ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
            RunConfig(),
        ),
    )
