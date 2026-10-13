"""S3 synthesizes the selected direction into one protected canonical design."""

from __future__ import annotations

from typing import TYPE_CHECKING

from experiment_design.agents import design
from experiment_design.contracts import Design, Patch, ReviewIssue, StageResult
from experiment_design.design.decisions import pending_reconciliation
from experiment_design.workflow.review import commit_revision
from experiment_design.workflow.stages.common import (
    BudgetStop,
    run_specialist,
    save_decisions,
    tool_context,
)

if TYPE_CHECKING:
    from experiment_design.workflow.dependencies import Deps


def run(run_id: str, deps: Deps) -> StageResult:
    store = deps.store
    if store.version(run_id) is None:
        ctx = tool_context(deps, run_id)
        payload = run_specialist(
            deps,
            run_id,
            design,
            ctx=ctx,
            mode="initial",
            selected_candidate=store.selected_candidate(run_id),
        ).findings_or_changes
        with store.transaction():
            store.check_binding(run_id, ctx.binding)
            version = store.add_version(
                run_id, Design.model_validate(payload["design"]), author="design", parent=None
            )
            save_decisions(store, run_id, payload)
            for decision in store.decisions(run_id):
                if (
                    decision.purpose == "scientific"
                    and decision.status == "APPLIED"
                    and decision.verification.get("status") == "pending"
                ):
                    issue = store.save_issue(
                        ReviewIssue(
                            run_id=run_id,
                            raised_by="researcher",
                            version_raised=version.version,
                            severity="MAJOR",
                            status="addressed",
                            addressed_in=version.version,
                            paths=decision.affected_paths or ["/"],
                            mechanism=(
                                f"Researcher decision {decision.id} requires reconciliation"
                            ),
                            consequence="Initial draft must implement clarified intent",
                            basis="reasoning",
                        )
                    )
                    decision.application["issue_id"] = issue.id
                    store.save_decision(decision)
            result = StageResult(
                stage_id="S3",
                status="COMPLETE",
                binding=store.binding(run_id),
                output_refs=[version.design_hash],
                reason="Structured draft committed",
            )
            store.commit_stage(run_id, result, "S4")
            return result
    else:
        refine_design(
            deps, run_id, mode="redesign" if store.selected_candidate(run_id) else "revision"
        )
        outputs = [store.version(run_id).design_hash]
    reason = "Structured draft committed"
    return StageResult(
        stage_id="S3",
        status="COMPLETE",
        binding=store.binding(run_id),
        output_refs=outputs,
        reason=reason,
    )


def refine_design(deps: Deps, run_id: str, *, mode: str = "revision"):
    issues = [
        i
        for i in deps.store.issues(run_id)
        if i.status in {"open", "unresolved"} and i.severity in {"MAJOR", "BLOCKER"}
    ]
    payload = run_specialist(
        deps,
        run_id,
        design,
        mode=mode,
        selected_candidate=deps.store.selected_candidate(run_id),
        batch=issues,
        researcher_decisions=[
            d.model_dump(mode="json") for d in pending_reconciliation(deps.store, run_id)
        ],
    ).findings_or_changes
    patch = Patch.model_validate(payload["patch"])
    parent = deps.store.version(run_id)
    with deps.store.transaction():
        if patch.ops:
            commit_revision(
                deps.store, run_id, parent=parent.version, patch=patch, issue_ids=patch.addresses
            )
        for rebuttal in payload.get("rebuttals", []):
            issue = next(i for i in issues if i.id == rebuttal["issue_id"])
            issue.status = "rebutted"
            issue.rebuttal = rebuttal["argument"]
            deps.store.save_issue(issue)
        save_decisions(deps.store, run_id, payload)
        deps.store.update_run(run_id, revisions_done=deps.store.run(run_id).revisions_done + 1)
    if deps.store.version(run_id).design_hash == parent.design_hash and not payload.get(
        "rebuttals"
    ):
        deps.store.append_event(
            run_id, "budget.stop", {"scope": "coordination", "reason": "no_progress"}
        )
        raise BudgetStop("no_progress")
