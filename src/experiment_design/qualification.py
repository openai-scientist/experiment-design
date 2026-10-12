"""Assess reviewed plan quality; execution belongs to the downstream stage."""

from uuid import uuid4

from experiment_design.contracts import (
    Approval,
    DecisionResponse,
    PublicationSnapshot,
    ReadinessAssessment,
    ReviewCohort,
    StageResult,
)
from experiment_design.decisions import respond
from experiment_design.package import all_sources
from experiment_design.runtime.store import Store, now
from experiment_design.science.validators import validate_design


def assess_readiness(store: Store, run_id: str) -> ReadinessAssessment:
    if store.run(run_id).plan_contract != "exp-plan/v1":
        raise ValueError("Historical runs are read/export only")
    snapshot = store.publication_snapshot(run_id)
    current = snapshot.version.design
    checks = validate_design(
        current,
        snapshot.context.package,
        source_ids={s.id for s in all_sources(snapshot.context, snapshot.decisions)},
        calculations=snapshot.calculations,
    )
    blockers = [f.message + ": " + f.path for f in checks if f.severity in {"MAJOR", "BLOCKER"}]
    if current.execution is None:
        blockers.append("Plan implementation boundaries and expected outputs are missing")
    for name, field in current.claim:
        if field.value is not None:
            annotation = snapshot.context.field_annotations.get(name)
            if not annotation or annotation.status not in {"source-stated", "researcher-confirmed"}:
                blockers.append(
                    f"Scientific intent {name} requires source support or researcher confirmation"
                )
    cohorts = [
        c
        for c in store._artifact_records(run_id, "cohort", ReviewCohort)
        if c.published_event_id is not None
        and c.binding.model_dump(exclude={"event_id"})
        == snapshot.binding.model_dump(exclude={"event_id"})
    ]
    if not cohorts:
        blockers.append("Current plan requires independent Methodologist and Skeptic review")
    blockers.extend(
        i.mechanism
        for i in snapshot.issues
        if i.severity in {"MAJOR", "BLOCKER"} and i.status not in {"resolved", "duplicate"}
    )
    pending = [
        d.id
        for d in snapshot.decisions
        if d.purpose != "final_review"
        and (d.status != "APPLIED" or d.verification.get("status") != "verified")
    ]
    # Numerical targets matter only when a supported bound calculation makes the comparison.
    scenarios = {s.name: s for s in current.scenarios}
    for calculation in snapshot.calculations:
        scenario = scenarios.get(calculation.scenario_id)
        if (
            scenario
            and scenario.target_power is not None
            and calculation.power is not None
            and calculation.power < scenario.target_power
        ):
            blockers.append(f"Recruitment for {scenario.name} falls below target power")
    assessment = ReadinessAssessment(
        id="assessment:" + uuid4().hex,
        run_id=run_id,
        binding=store.binding(run_id),
        review_status="NEEDS_INPUT" if pending else "REVIEWABLE" if cohorts else "NOT_READY",
        plan_complete=not blockers and not pending,
        blockers=list(dict.fromkeys(blockers)),
        decision_ids=pending,
        computation_refs=[c.id for c in snapshot.calculations],
        evidence_refs=[c.id for c in cohorts],
    )
    return store.save_assessment(assessment)


def can_approve(snapshot: PublicationSnapshot) -> list[str]:
    reasons = []
    assessment = snapshot.assessment
    if (
        assessment is None
        or assessment.review_status != "REVIEWABLE"
        or assessment.plan_complete is not True
    ):
        reasons.append("approval requires current REVIEWABLE and complete reviewed plan")
    if assessment and assessment.binding.model_dump(
        exclude={"event_id"}
    ) != snapshot.binding.model_dump(exclude={"event_id"}):
        reasons.append("approval assessment version or context is stale")
    if assessment and assessment.blockers:
        reasons.append("scientific plan blockers remain")
    if any(
        i.severity in {"MAJOR", "BLOCKER"} and i.status not in {"resolved", "duplicate"}
        for i in snapshot.issues
    ):
        reasons.append("material issues remain unresolved")
    if any(
        d.purpose != "final_review"
        and (d.status != "APPLIED" or d.verification.get("status") != "verified")
        for d in snapshot.decisions
    ):
        reasons.append("researcher decisions require application and reconciliation")
    return reasons


def approve_version(
    store: Store, run_id: str, decision_id: str, response: DecisionResponse
) -> Approval:
    with store.transaction():
        decision = store.decision(run_id, decision_id)
        store.check_binding(run_id, decision.binding)
        if decision.purpose != "final_review" or response.action != "APPROVE_VERSION":
            raise ValueError("approval requires final review decision")
        snapshot = store.publication_snapshot(run_id)
        reasons = can_approve(snapshot)
        if reasons:
            raise ValueError("; ".join(reasons))
        if decision.dependencies and snapshot.assessment.id not in decision.dependencies:
            raise ValueError("approval requires the presented assessment")
        answered = respond(store, run_id, decision_id, response)
        if answered.application:
            return store.approvals(run_id)[-1]
        approval = Approval(
            run_id=run_id,
            version=snapshot.version.version,
            design_hash=snapshot.version.design_hash,
            approver=response.researcher,
            at=now(),
        )
        answered.status = "APPLIED"
        answered.application = {
            "response_id": response.response_id,
            "assessment_id": snapshot.assessment.id,
        }
        store.save_decision(answered)
        store.add_approval(approval)
        stage = store.run(run_id).stage_id
        if stage not in {"S6", "S7"}:
            raise ValueError("approval requires final researcher review stage")
        if stage == "S6":
            store.commit_stage(
                run_id,
                StageResult(
                    stage_id="S6",
                    status="COMPLETE",
                    binding=store.binding(run_id),
                    output_refs=[decision.id],
                    reason="Exact version approved by researcher",
                ),
                "S7",
            )
        store.update_run(run_id, status="APPROVED")
        store.freeze_publication(run_id)
        return approval
