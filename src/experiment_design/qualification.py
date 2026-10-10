"""Version-bound scientific qualification and explicit final researcher approval."""

from uuid import uuid4

from experiment_design.contracts import (
    AgentResult,
    Approval,
    DecisionResponse,
    PublicationSnapshot,
    ReadinessAssessment,
    ReviewIssue,
    StageResult,
)
from experiment_design.decisions import respond
from experiment_design.runtime.store import Store, now
from experiment_design.science.validators import validate_design


def assess_readiness(store: Store, run_id: str, handoff_result: AgentResult) -> ReadinessAssessment:
    snapshot = store.publication_snapshot(run_id)
    binding = store.binding(run_id)
    payload = handoff_result.findings_or_changes or {}
    checks = validate_design(
        snapshot.version.design, snapshot.context.package, calculations=snapshot.calculations
    )
    blockers = [f.message for f in checks if f.severity in {"MAJOR", "BLOCKER"}]
    for name in (
        "hypothesis",
        "primary_outcome",
        "outcome_unit",
        "outcome_kind",
        "direction",
        "contrast_type",
        "estimand",
    ):
        annotation = snapshot.context.field_annotations.get(name)
        if not annotation or annotation.status not in {"source-stated", "researcher-confirmed"}:
            blockers.append(
                f"Scientific intent {name} requires source support or researcher confirmation"
            )
    blockers.extend(snapshot.context.unknowns)
    scenarios = {s.name: s for s in snapshot.version.design.scenarios}
    covered = {c.scenario_id for c in snapshot.calculations}
    if set(scenarios) - covered:
        blockers.append("Current scenarios lack bound calculations")
    for calculation in snapshot.calculations:
        scenario = scenarios.get(calculation.scenario_id)
        if scenario and calculation.power is not None and calculation.power < scenario.target_power:
            blockers.append(f"Recruitment for {scenario.name} falls below target power")
    blockers.extend(payload.get("risks", []))
    for finding in payload.get("findings", []):
        store.save_issue(
            ReviewIssue(
                **finding,
                run_id=run_id,
                raised_by="handoff",
                version_raised=snapshot.version.version,
            )
        )
    blockers += [
        i.mechanism
        for i in store.issues(run_id)
        if i.severity in {"MAJOR", "BLOCKER"} and i.status not in {"resolved", "duplicate"}
    ]
    pending = [
        d.id
        for d in snapshot.decisions
        if d.purpose != "final_review"
        and (d.status != "APPLIED" or d.verification.get("status") != "verified")
    ]
    supported = bool(snapshot.calculations) and all(
        c.status == "computed" and c.power is not None for c in snapshot.calculations
    )
    complete = (
        handoff_result.completion_status == "completed"
        and payload.get("handoff_complete") is True
        and supported
        and not blockers
        and not pending
    )
    if not supported:
        blockers.append("Bound recruitment/power computation is missing or unsupported")
    assessment = ReadinessAssessment(
        id="assessment:" + uuid4().hex,
        run_id=run_id,
        binding=binding,
        review_status="NEEDS_INPUT" if pending else "NOT_READY" if blockers else "REVIEWABLE",
        handoff_complete=complete,
        blockers=blockers,
        decision_ids=pending,
        computation_refs=[c.id for c in snapshot.calculations],
        handoff_risks=payload.get("risks", []),
        evidence_refs=handoff_result.evidence_refs,
    )
    return store.save_assessment(assessment)


def can_approve(snapshot: PublicationSnapshot) -> list[str]:
    reasons = []
    assessment = snapshot.assessment
    if (
        assessment is None
        or assessment.review_status != "REVIEWABLE"
        or not assessment.handoff_complete
    ):
        reasons.append("approval requires current REVIEWABLE and complete handoff")
    if assessment and (
        assessment.binding.design_hash != snapshot.binding.design_hash
        or assessment.binding.context_revision != snapshot.binding.context_revision
    ):
        reasons.append("approval assessment version or context is stale")
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
    if not snapshot.calculations or any(c.status != "computed" for c in snapshot.calculations):
        reasons.append("unsupported or missing calculation")
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
        if store.run(run_id).stage_id != "S6":
            raise ValueError("approval requires final researcher review stage")
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
        return approval
