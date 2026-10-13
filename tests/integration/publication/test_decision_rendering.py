"""Stored scientific choices must be usable without reverse engineering JSON."""

from datetime import UTC, datetime

from experiment_design.contracts import (
    AgentResult,
    AgentTask,
    Assumption,
    DecisionResponse,
    DesignChoice,
    Quantity,
    ReadinessAssessment,
    TaskBudget,
)
from experiment_design.persistence.store import TaskRecord
from experiment_design.publication import render_markdown
from experiment_design.publication.history import render_stage_artifacts
from tests.support.presentation import choice, study
from tests.support.scenarios import supported_design


def test_bound_choices_and_exact_response_provenance_in_both_markdown_outputs(tmp_path):
    store, rid = study(tmp_path)
    try:
        decision = choice(store, rid)
        decision.responses = [
            DecisionResponse(
                revision=2,
                action="PROPOSE_ALTERNATIVE",
                text="Previous exact response, pending reconciliation",
                researcher="Researcher Linh",
                response_id="reply-2",
                at=datetime(2026, 10, 11, 2, 3, tzinfo=UTC),
            )
        ]
        store.save_decision(decision)
        snapshot = store.publication_snapshot(rid)
        texts = [
            render_markdown(snapshot),
            render_stage_artifacts(store.stage_artifacts(rid, snapshot)),
        ]
        for text in texts:
            for value in (
                "estimand-choice",
                "revision 3",
                "AWAITING_RESEARCHER",
                "/claim/estimand",
                "/analysis/model",
                "Population and assignment interpretation",
                "Consider assigned treatment; confirmation still needed",
                "itt",
                "Assigned treatment effect",
                "Effect in randomized students",
                "Retains randomized comparison",
                "Adherence does not define the target population",
                "S1",
                "Previous exact response, pending reconciliation",
                "Researcher Linh",
                "revision 2",
                "2026-10-11T02:03:00+00:00",
                "reply-2",
            ):
                assert value in text
            assert "Recommendation (provisional)" in text
            assert "context revision 1" in text.lower()
            assert "checkpoint" in text.lower()
        assert snapshot.approval is None
        assert store.decision(rid, decision.id) == decision
    finally:
        store.close()


def test_scientific_stage_projection_preserves_rationale_and_critique_evidence(tmp_path):
    store, rid = study(tmp_path)
    try:
        d = supported_design()
        d.rationale = [
            DesignChoice(
                choice="Class assignment",
                why="Avoid within-class contamination",
                alternatives_considered=["Individual assignment risks spillover"],
                tradeoffs="Fewer independent units",
            )
        ]
        d.assumptions = [
            Assumption(
                id="A-ICC",
                statement="ICC requires confirmation",
                quantity=Quantity(value=0.1, provenance="assumption", note="Planning value only"),
            )
        ]
        store.add_version(rid, d, author="design", parent=1)
        binding = store.binding(rid)
        task = AgentTask(
            id="review-task",
            stage_id="S4",
            binding=binding,
            objective="Review clustering",
            package_ref=rid,
            design_version=2,
            allowed_tools=[],
            budget=TaskBudget(max_turns=1, max_tokens=100),
            exit_conditions=["Review submitted"],
        )
        store.append_event(rid, "task.started", {}, task_id=task.id, agent_id="skeptic")
        store.save_task(
            rid,
            TaskRecord(
                task=task,
                submission=AgentResult(
                    completion_status="completed",
                    findings_or_changes={
                        "summary": "Test cluster uncertainty",
                        "scenarios_checked": ["tool:review-task:icc-sensitivity"],
                        "findings": [
                            {
                                "severity": "MAJOR",
                                "paths": ["/sampling/cluster"],
                                "mechanism": "Unequal sizes may increase variance",
                                "consequence": "Nominal precision may be optimistic",
                                "basis": "unknown_assumption",
                                "evidence_refs": ["S1"],
                                "suggested_fix": "Verify the class-size distribution",
                            }
                        ],
                    },
                ),
            ),
        )
        store.save_assessment(
            ReadinessAssessment(
                id="assessment",
                run_id=rid,
                binding=store.binding(rid),
                review_status="NEEDS_INPUT",
                handoff_complete=False,
                blockers=["Confirm instrument scoring before downstream implementation"],
                handoff_risks=["Confirm instrument scoring before downstream implementation"],
                evidence_refs=["tool:handoff-task:design-inspection"],
            )
        )
        snapshot = store.publication_snapshot(rid)
        before = snapshot.model_dump(mode="json")
        edp = render_markdown(snapshot)
        stages = render_stage_artifacts(store.stage_artifacts(rid, snapshot))
        assert "Individual assignment risks spillover" in edp
        assert "Planning value only" in edp
        s2 = stages.split("## S2")[1].split("## S3")[0]
        assert "Lower bound exceeds -3 points" in s2
        assert "source-stated" in s2 and "S1" in s2
        assert "At most 60 classes" in s2
        assert "review-task" in stages and "checkpoint" in stages
        assert "/sampling/cluster" in stages and "unknown_assumption" in stages
        assert "Verify the class-size distribution" in stages
        assert "tool:review-task:icc-sensitivity" in stages
        for text in (edp, stages):
            assert text.count("Confirm instrument scoring before downstream implementation") == 1
            assert "tool:handoff-task:design-inspection" in text
        assert snapshot.model_dump(mode="json") == before
        assert store.publication_snapshot(rid).model_dump(mode="json") == before
    finally:
        store.close()
