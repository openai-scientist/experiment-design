import pytest
from test_decisions import decision_state

from experiment_design.contracts import AgentResult, DecisionResponse, ResearcherDecision
from experiment_design.qualification import approve_version, assess_readiness, can_approve


def test_incomplete_handoff_cannot_approve(tmp_path):
    store, run, context, decision = decision_state(tmp_path)
    assessment = assess_readiness(
        store,
        run,
        AgentResult(
            completion_status="completed",
            findings_or_changes={
                "handoff_complete": False,
                "risks": ["SAP mixed model unsupported"],
                "findings": [],
                "evidence_refs": [],
            },
        ),
    )
    assert not assessment.handoff_complete
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
    binding = store.binding(run)
    result = calculate(CalculationRequest(binding=binding, scenario_id="base", design=d))
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
    assessment = assess_readiness(
        store,
        run,
        AgentResult(
            completion_status="completed",
            findings_or_changes={
                "handoff_complete": True,
                "risks": [],
                "findings": [],
                "evidence_refs": ["tool:qualification-power"],
            },
        ),
    )
    assert assessment.handoff_complete
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


def test_supported_but_underpowered_recruitment_is_not_complete(tmp_path):
    from experiment_design.contracts import AgentResult, CalculationRequest, Observation
    from experiment_design.science.power import calculate

    store, run, context, decision = decision_state(tmp_path)
    decision.status = "APPLIED"
    decision.verification = {"status": "verified"}
    store.save_decision(decision)
    from test_stage_pipeline import supported_design

    d = supported_design()
    d.sampling.cluster.clusters_per_arm = 10
    store.add_version(run, d, author="design", parent=1)
    binding = store.binding(run)
    calculation = calculate(CalculationRequest(binding=binding, scenario_id="base", design=d))
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
    assessment = assess_readiness(
        store,
        run,
        AgentResult(
            completion_status="completed",
            findings_or_changes={
                "handoff_complete": True,
                "risks": [],
                "findings": [],
                "evidence_refs": [],
            },
        ),
    )
    assert not assessment.handoff_complete
    assert any("target power" in reason for reason in assessment.blockers)
    store.close()
