"""Pure authority policy for exact reviewed-plan approval."""

from experiment_design.contracts import PublicationSnapshot


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
