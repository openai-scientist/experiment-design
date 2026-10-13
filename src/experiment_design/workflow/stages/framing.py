"""S2 grounds intent and explores alternatives before committing a design."""

from __future__ import annotations

from typing import TYPE_CHECKING

from experiment_design.agents import explorer, understanding
from experiment_design.config import RunConfig
from experiment_design.contracts import ResearchContext, ResearcherDecision, StageResult
from experiment_design.design.decisions import pending_reconciliation
from experiment_design.design.exploration import ExplorationSelection, recommendation_options
from experiment_design.runtime.jev import Question
from experiment_design.workflow.stages.common import (
    BudgetStop,
    choose_next_action,
    run_specialist,
    save_decisions,
)

if TYPE_CHECKING:
    from experiment_design.workflow.dependencies import Deps


def run(run_id: str, deps: Deps) -> StageResult:

    store = deps.store
    deps.run = RunConfig.model_validate(store.run(run_id).config.get("run", deps.run.model_dump()))
    entry = next(
        (
            e.event_id
            for e in reversed(store.events(run_id))
            if (
                e.type == "stage.result"
                and e.data.get("target") == "S2"
                and e.data.get("stage_id") != "S2"
            )
        ),
        store.events(run_id)[0].event_id,
    )
    frames = store._artifact_records(run_id, "framing", StageResult)
    binding = store.binding(run_id)
    framed = next(
        (
            f
            for f in reversed(frames)
            if (
                f.reason == f"entry:{entry}"
                and f.binding.model_dump(exclude={"event_id"})
                == binding.model_dump(exclude={"event_id"})
            )
        ),
        None,
    )
    if framed is None:
        payload = run_specialist(
            deps,
            run_id,
            understanding,
            entry_ref=entry,
            researcher_decisions=[
                d.model_dump(mode="json") for d in pending_reconciliation(store, run_id)
            ],
        ).findings_or_changes
        with store.transaction():
            context = store.save_context(run_id, ResearchContext.model_validate(payload["context"]))
            for decision_id, unknowns in payload.get("resolved_unknowns", {}).items():
                decision = store.decision(run_id, decision_id)
                audits = list(decision.reconciliation.get("unknown_resolutions", []))
                audits.append(
                    {
                        "decision_id": decision_id,
                        "response_id": decision.responses[-1].response_id,
                        "context_revision": context.revision,
                        "unknowns": unknowns,
                    }
                )
                decision.reconciliation = {**decision.reconciliation, "unknown_resolutions": audits}
                store.save_decision(decision)
            save_decisions(store, run_id, payload)
            framed = StageResult(
                stage_id="S2",
                status="COMPLETE",
                binding=store.binding(run_id),
                output_refs=[f"context:{context.revision}"],
                reason=f"entry:{entry}",
            )
            store._put_artifact(run_id, "framing", f"{entry}:{context.revision}", framed)
    pending = [
        d
        for d in store.decisions(run_id)
        if d.status in {"IDENTIFIED", "EXPLORING", "AWAITING_RESEARCHER"}
    ]
    choice = "CONTINUE"
    if pending:
        options = {x: x for x in ("ASK_RESEARCHER", "CONTINUE", "STOP")}
        choice = choose_next_action(
            deps,
            run_id,
            "understanding",
            {
                "next_action": Question(
                    "Clarify material scientific intent or dependencies; "
                    "do not ask for every methodological proposal.",
                    options,
                )
            },
            proposed_understanding={
                "context": store.context(run_id).model_dump(mode="json"),
                "decisions": [d.model_dump(mode="json") for d in pending],
            },
        )["next_action"]
    outputs = list(framed.output_refs)
    status = "COMPLETE"
    reason = "Grounded research direction recorded"
    if choice == "ASK_RESEARCHER":
        for decision in pending:
            if decision.status != "AWAITING_RESEARCHER":
                decision.status = "AWAITING_RESEARCHER"
                store.save_decision(decision)
        status, reason = "NEEDS_INPUT", "Research understanding needs a scientific decision"
    elif choice == "STOP":
        status, reason = "STOPPED", "Research understanding stopped with uncertainty preserved"
    else:
        context = store.context(run_id)
        method = context.package.hypothesis.method
        annotation = context.field_annotations.get("/analysis/model")
        fixed = bool(
            method and annotation and annotation.status in {"source-stated", "researcher-confirmed"}
        )
        if not fixed:
            version = store.binding(run_id).design_version or 0
            task_id = f"explore:{run_id}:{entry}:{context.revision}:{version}"
            frozen = store.exploration_snapshot(run_id, task_id=task_id)
            if frozen is None:
                issues = [
                    i
                    for i in store.issues(run_id)
                    if i.status in {"open", "unresolved"} and i.severity in {"MAJOR", "BLOCKER"}
                ]
                payload = run_specialist(
                    deps, run_id, explorer, task_id=task_id, issues=issues
                ).findings_or_changes
                from experiment_design.design.exploration import ExplorationSubmission

                submission = ExplorationSubmission.model_validate(payload)
                selection = store.exploration_selection(run_id, task_id=task_id)
                if selection is None:
                    next_probe = choose_next_action(
                        deps,
                        run_id,
                        "exploration",
                        {
                            "next_probe": Question(
                                "Choose the most informative feasible scientific direction; "
                                "retain conditions and limits.",
                                recommendation_options(submission),
                            )
                        },
                        proposed_understanding=payload,
                    )["next_probe"]
                    selection = ExplorationSelection(
                        task_id=task_id, binding=store.task(task_id).task.binding, choice=next_probe
                    )
                    store.save_exploration_selection(run_id, selection)
                if store.usage(run_id).llm_tokens > deps.run.llm_tokens:
                    store.append_event(
                        run_id, "budget.stop", {"scope": "run", "reason": "token_limit"}
                    )
                    raise BudgetStop("token_limit")
                frozen = store.freeze_exploration(run_id, task_id)
            outputs.append(frozen.id)
            if frozen.outcome == "needs_input":
                decision_id = f"exploration-question:{task_id}"
                if not any(d.id == decision_id for d in store.decisions(run_id)):
                    store.save_decision(
                        ResearcherDecision(
                            id=decision_id,
                            run_id=run_id,
                            binding=store.binding(run_id),
                            status="AWAITING_RESEARCHER",
                            problem="; ".join(frozen.submission.focused_questions),
                            affected_paths=["/context/package/constraints"],
                        )
                    )
                status, reason = "NEEDS_INPUT", frozen.submission.defer_reason
    result = StageResult(
        stage_id="S2",
        status=status,
        binding=store.binding(run_id),
        output_refs=outputs,
        reason=reason,
    )
    store.commit_stage(run_id, result, "S3" if status == "COMPLETE" else "S2")
    return result
