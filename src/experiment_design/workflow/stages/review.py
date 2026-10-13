"""S4 owns independent critique, grounded refinement and direction rejection."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from experiment_design.agents import design, methodologist, skeptic, verifier
from experiment_design.agents.tools import ToolContext, source_ids
from experiment_design.contracts import ReviewCohort, ReviewIssue, StageResult
from experiment_design.persistence.store import TaskRecord
from experiment_design.runtime.jev import Question
from experiment_design.science.validators import validate_design
from experiment_design.workflow.review import (
    publish_cohort,
    review_actions,
    review_context,
    start_cohort,
    verification_options,
)
from experiment_design.workflow.stages.common import (
    choose_next_action,
    remaining_budget,
    run_specialist,
    tool_context,
)
from experiment_design.workflow.stages.synthesis import refine_design

if TYPE_CHECKING:
    from experiment_design.workflow.dependencies import Deps


def _cohort(deps: Deps, run_id: str):
    cohorts = deps.store._artifact_records(run_id, "cohort", ReviewCohort)
    cohort = cohorts[-1] if cohorts else None
    if (
        cohort
        and cohort.published_event_id is None
        and (
            cohort.binding.model_dump(exclude={"event_id"})
            != deps.store.binding(run_id).model_dump(exclude={"event_id"})
        )
    ):
        with deps.store.transaction():
            replacement = start_cohort(deps.store, run_id, deps.store.binding(run_id))
            cohort.superseded_by = replacement.id
            deps.store.save_cohort(cohort)
        cohort = replacement
    if cohort is None or cohort.published_event_id is not None:
        cohort = start_cohort(deps.store, run_id, deps.store.binding(run_id))
    for role, module in (("methodologist", methodologist), ("skeptic", skeptic)):
        task_id = cohort.member_task_ids[role]
        try:
            task = deps.store.task(task_id).task
            ctx = review_context(deps.store, run_id, task)
        except KeyError:
            context = deps.store.context(run_id, cohort.binding.context_revision)
            ctx = ToolContext(
                deps.store,
                run_id,
                context.package,
                deps.store.version(run_id, cohort.binding.design_version),
                set(),
                binding=cohort.binding,
            )
            ctx.binding = cohort.binding
            ctx.cohort_id = cohort.id
            ctx.issue_cutoff = cohort.binding.event_id
        run_specialist(deps, run_id, module, ctx=ctx, task_id=task_id)
    with deps.store.transaction():
        publish_cohort(deps.store, run_id, cohort.id)
        run = deps.store.run(run_id)
        deps.store.update_run(
            run_id,
            reviewed_version=deps.store.version(run_id).version,
            reviews_done=run.reviews_done + 1,
        )


def _verify(deps: Deps, run_id: str):
    issues = [i for i in deps.store.issues(run_id) if i.status in {"addressed", "rebutted"}]
    issue_ids = {i.id for i in issues}
    clarifications = [
        {
            "decision_id": d.id,
            "affected_paths": d.affected_paths,
            "response": d.responses[-1].model_dump(mode="json"),
            "application": d.application,
            "reconciliation": d.reconciliation,
        }
        for d in deps.store.decisions(run_id)
        if d.application.get("issue_id") in issue_ids and d.responses
    ]
    comparisons = []
    for issue in issues:
        addressed = issue.addressed_in
        version = deps.store.version(run_id, addressed) if addressed is not None else None
        if addressed is None:
            application = next(
                (
                    d["application"]
                    for d in clarifications
                    if d["application"].get("issue_id") == issue.id
                ),
                {},
            )
            recorded = application.get("version")
            if recorded is not None:
                version = deps.store.version(run_id, recorded)
                if version is not None:
                    addressed = recorded
        comparisons.append(
            {
                "issue_id": issue.id,
                "addressed_version": addressed,
                "parent_version": version.parent if version else None,
            }
        )
    # Reuse accepted submissions; do not copy a second rebuttal/evidence ledger into issues.
    rebuttals = {}
    arguments = {i.id: i.rebuttal for i in issues if i.status == "rebutted"}
    if arguments:
        records = deps.store._artifact_records(run_id, "task", TaskRecord)
        designer_tasks = {
            e.task_id
            for e in deps.store.events(run_id)
            if e.type == "task.started" and e.agent_id == design.SPEC.agent_id
        }
        for record in reversed(records):
            if (
                record.submission is None
                or record.task.id not in designer_tasks
                or record.task.binding is None
            ):
                continue
            for defense in (record.submission.findings_or_changes or {}).get("rebuttals", []):
                issue_id = defense["issue_id"]
                if issue_id in rebuttals or arguments.get(issue_id) != defense["argument"]:
                    continue
                refs = defense["evidence_refs"]
                rebuttals[issue_id] = {
                    **defense,
                    "task_id": record.task.id,
                    "binding": record.task.binding.model_dump(mode="json"),
                    "observations": [
                        o.model_dump(mode="json")
                        for o in deps.store.accepted_observations(record.task.id)
                        if o.evidence_ref in refs and o.binding == record.task.binding
                    ],
                    "sources": [
                        s.model_dump(mode="json")
                        for s in deps.store.bound_sources(run_id, record.task.binding)
                        if s.id in refs
                    ],
                }
    result = run_specialist(
        deps,
        run_id,
        verifier,
        issues=issues,
        comparisons=comparisons,
        researcher_clarifications=clarifications,
        rebuttals=list(rebuttals.values()),
    )
    live = validate_design(
        deps.store.version(run_id).design,
        deps.store.context(run_id).package,
        source_ids(tool_context(deps, run_id)),
    )
    questions = {}
    verdicts = {v["issue_id"]: v for v in result.findings_or_changes["verdicts"]}
    for issue in issues:
        verdict = verdicts[issue.id]
        options = verification_options(issue, verdict["verdict"], live, verdict["evidence_refs"])
        questions[issue.id] = Question(
            "Select disposition supported by independent verification: " + json.dumps(verdict),
            {v: v for v in options},
        )
    outcomes = choose_next_action(deps, run_id, "verification", questions)
    with deps.store.transaction():
        for issue in issues:
            issue.status = outcomes[issue.id]
            deps.store.save_issue(issue)
            deps.store.append_event(
                run_id,
                "verification",
                {"issue_id": issue.id, "effective_outcome": issue.status, **verdicts[issue.id]},
            )
        for finding in result.findings_or_changes["regressions"]:
            deps.store.save_issue(
                ReviewIssue(
                    **finding,
                    run_id=run_id,
                    raised_by="verifier",
                    version_raised=deps.store.version(run_id).version,
                )
            )
        for decision in deps.store.decisions(run_id):
            issue_id = decision.application.get("issue_id")
            if (
                decision.status == "APPLIED"
                and decision.purpose == "scientific"
                and issue_id in outcomes
            ):
                decision.verification = {
                    "status": "verified"
                    if outcomes[issue_id] == "resolved"
                    else outcomes[issue_id],
                    "evidence_refs": verdicts[issue_id]["evidence_refs"],
                }
                deps.store.save_decision(decision)


def run(run_id: str, deps: Deps) -> StageResult:
    store = deps.store
    outputs, issues, status, reason = [], [], "COMPLETE", ""
    while True:
        remaining_budget(deps, run_id)
        cohorts = store._artifact_records(run_id, "cohort", ReviewCohort)
        pending_cohort = bool(cohorts and cohorts[-1].published_event_id is None)
        actions = review_actions(store, run_id, deps.run)
        action = (
            "REVIEW"
            if pending_cohort
            else choose_next_action(
                deps,
                run_id,
                "next_action",
                {
                    "next_action": Question(
                        "Perform independent REVIEW before qualification. QUALIFY assesses a "
                        "reviewed draft with unresolved limitations; it does not authorize "
                        "execution. Prefer qualification when revision needs unavailable "
                        "evidence. STOP when useful review or draft handoff cannot proceed. "
                        "VERIFY actual changes before claiming issue resolution. REEXPLORE when "
                        "findings undermine the direction, not merely its implementation.",
                        {a: a for a in actions},
                    )
                },
            )["next_action"]
        )
        if action == "QUALIFY":
            outputs, reason = (
                [store.version(run_id).design_hash]
                + [
                    c.id
                    for c in store._artifact_records(run_id, "cohort", ReviewCohort)
                    if c.published_event_id is not None
                ],
                "Review permits independent qualification",
            )
            break
        if action == "REEXPLORE":
            material = [
                i.id
                for i in store.issues(run_id)
                if i.status in {"open", "unresolved"} and i.severity in {"MAJOR", "BLOCKER"}
            ]
            result = StageResult(
                stage_id="S4",
                status="REVISION_REQUIRED",
                binding=store.binding(run_id),
                issue_refs=material,
                reason="Independent critique rejects the current direction; compare alternatives",
            )
            store.commit_stage(run_id, result, "S2")
            store.update_run(run_id, actions_done=store.run(run_id).actions_done + 1)
            return result
        if action == "STOP":
            status, reason = "STOPPED", "Scientific coordination stopped with issues preserved"
            break
        if action == "ASK_RESEARCHER":
            for decision in store.decisions(run_id):
                if decision.status in {"IDENTIFIED", "EXPLORING"}:
                    decision.binding = store.binding(run_id)
                    decision.status = "AWAITING_RESEARCHER"
                    store.save_decision(decision)
            status, reason = "NEEDS_INPUT", "Scientific decision requires researcher input"
            break
        {"REVIEW": _cohort, "REVISE": refine_design, "VERIFY": _verify}[action](deps, run_id)
        store.update_run(run_id, actions_done=store.run(run_id).actions_done + 1)
    return StageResult(
        stage_id="S4",
        status=status,
        binding=store.binding(run_id),
        output_refs=outputs,
        issue_refs=issues,
        reason=reason,
    )
