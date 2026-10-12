"""S5 aggregates existing scientific work and routes actionable feedback."""

from __future__ import annotations

from typing import TYPE_CHECKING

from experiment_design.contracts import ResearcherDecision, ReviewIssue, StageResult
from experiment_design.package import all_sources
from experiment_design.qualification import assess_readiness
from experiment_design.runtime.jev import Question
from experiment_design.science.validators import validate_design
from experiment_design.stages.common import choose_next_action, remaining_budget

if TYPE_CHECKING:
    from experiment_design.workflow import Deps


def run(run_id: str, deps: Deps) -> StageResult:
    store = deps.store
    snapshot = store.publication_snapshot(run_id)
    checks = validate_design(
        snapshot.version.design,
        snapshot.context.package,
        source_ids={s.id for s in all_sources(snapshot.context, snapshot.decisions)},
        calculations=snapshot.calculations,
    )
    with store.transaction():
        for check in checks:
            if check.severity not in {"MAJOR", "BLOCKER"}:
                continue
            if any(
                i.validator_code == check.code
                and i.paths == [check.path]
                and i.status not in {"resolved", "duplicate"}
                for i in store.issues(run_id)
            ):
                continue
            store.save_issue(
                ReviewIssue(
                    run_id=run_id,
                    raised_by="qualification",
                    version_raised=snapshot.version.version,
                    severity=check.severity,
                    paths=[check.path],
                    mechanism=check.message,
                    consequence=check.consequence,
                    basis="reasoning",
                    validator_code=check.code,
                )
            )
        for name, field in snapshot.version.design.claim:
            annotation = snapshot.context.field_annotations.get(name)
            if field.value is not None and (
                not annotation or annotation.status not in {"source-stated", "researcher-confirmed"}
            ):
                path = f"/claim/{name}"
                if not any(
                    path in d.affected_paths and d.status != "APPLIED"
                    for d in store.decisions(run_id)
                ):
                    store.save_decision(
                        ResearcherDecision(
                            id=f"intent:{store.run(run_id).context_revision}:{name}",
                            run_id=run_id,
                            binding=store.binding(run_id),
                            status="AWAITING_RESEARCHER",
                            problem=f"Clarify proposed scientific intent: {name} = {field.value}",
                            affected_paths=[path],
                        )
                    )
        for decision in store.decisions(run_id):
            if decision.purpose == "scientific" and decision.status in {"IDENTIFIED", "EXPLORING"}:
                decision.binding = store.binding(run_id)
                decision.status = "AWAITING_RESEARCHER"
                store.save_decision(decision)
        assessment = assess_readiness(store, run_id)
    waiting = [
        d.id
        for d in store.decisions(run_id)
        if d.purpose == "scientific" and d.status == "AWAITING_RESEARCHER"
    ]
    if waiting:
        return StageResult(
            stage_id="S5",
            status="NEEDS_INPUT",
            binding=store.binding(run_id),
            output_refs=waiting,
            reason="Scientific intent needs researcher clarification",
        )
    issues = [
        i
        for i in store.issues(run_id)
        if i.severity in {"MAJOR", "BLOCKER"} and i.status not in {"resolved", "duplicate"}
    ]
    origins = list(issues)
    if assessment.review_status == "NOT_READY":
        review_gap = next(
            (i for i in store.issues(run_id) if i.validator_code == "independent_review_missing"),
            None,
        )
        if review_gap is None:
            review_gap = store.save_issue(
                ReviewIssue(
                    run_id=run_id,
                    raised_by="qualification",
                    version_raised=snapshot.version.version,
                    severity="MINOR",
                    paths=["/"],
                    mechanism="Current plan needs independent review",
                    consequence="Plan quality has not been independently assessed",
                    basis="reasoning",
                    validator_code="independent_review_missing",
                )
            )
        origins.append(review_gap)
    options = {"REVIEW_PLAN": "Present the assessed plan and disclosed blockers to the researcher"}
    if issues and store.run(run_id).revisions_done < deps.run.max_revisions:
        options.update(
            {
                "REDESIGN": "Fill scientific procedure gaps in S3",
                "REEXPLORE": "Reconsider direction in S2",
            }
        )
    if origins and store.run(run_id).reviews_done < deps.run.max_reviews:
        options["CRITIQUE"] = "Resolve review dispositions in S4"
    action = "REVIEW_PLAN"
    if len(options) > 1:
        remaining_budget(deps, run_id)
        action = choose_next_action(
            deps,
            run_id,
            "qualification",
            {
                "next_action": Question(
                    "Route scientific blockers to their owner. Re-explore if direction fails; "
                    "redesign gaps; critique unresolved findings. Present a draft when further "
                    "work lacks evidence or progress. Do not loop over minor uncertainty.",
                    options,
                )
            },
        )["next_action"]
    if action != "REVIEW_PLAN":
        target = {"REDESIGN": "S3", "REEXPLORE": "S2", "CRITIQUE": "S4"}[action]
        result = StageResult(
            stage_id="S5",
            status="REVISION_REQUIRED",
            binding=store.binding(run_id),
            output_refs=[assessment.id],
            issue_refs=[i.id for i in origins],
            reason=f"Qualification feedback: {action}",
        )
        store.commit_stage(run_id, result, target)
        store.update_run(run_id, actions_done=store.run(run_id).actions_done + 1)
        return result
    result = StageResult(
        stage_id="S5",
        status="COMPLETE",
        binding=store.binding(run_id),
        output_refs=[assessment.id],
        issue_refs=[i.id for i in issues],
        reason="; ".join(assessment.blockers) or "Reviewed scientific plan is complete",
    )
    store.commit_stage(run_id, result, "S6")
    return result
