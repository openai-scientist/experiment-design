"""Owned stage progression with scientific coordination confined to review/refinement."""

from dataclasses import dataclass
from uuid import uuid4

from experiment_design.agents import (
    decision_interpretation,
    design,
)
from experiment_design.config import RunConfig
from experiment_design.contracts import (
    DecisionResponse,
    Patch,
    ResearcherDecision,
    ReviewCohort,
    ReviewIssue,
    RunStatus,
    SourceSnapshot,
    StageId,
    StageResult,
)
from experiment_design.decisions import (
    apply_decision,
    interpret,
    reconcile_answer,
    respond,
)
from experiment_design.patch import scientific_path
from experiment_design.qualification import approve_version
from experiment_design.review import (
    review_actions,
)
from experiment_design.runtime.jev import (
    Judge,
    JudgeBudgetStop,
)
from experiment_design.runtime.llm import LLMClient
from experiment_design.runtime.store import Store
from experiment_design.stages.common import (
    BudgetStop,
    run_specialist,
)


@dataclass
class Deps:
    store: Store
    llm: LLMClient
    judge: Judge
    run: RunConfig


class WorkflowError(ValueError):
    pass


def start_run(snapshot: SourceSnapshot, deps: Deps, config_snapshot: dict) -> str:
    return deps.store.create_staged_run(snapshot, config_snapshot | {"run": deps.run.model_dump()})


def _require_staged(store: Store, run_id: str) -> None:
    if store.is_exploration_run(run_id):
        raise WorkflowError("exploration proposals cannot enter canonical plan workflow")
    if store.run(run_id).schema_version != 2:
        raise WorkflowError(
            "schema-1 runs are read/export only; start a new run from original sources"
        )
    if store.run(run_id).plan_contract != "exp-plan/v1":
        raise WorkflowError("Historical runs are read/export only; start a new run from sources")


def legal_actions(store: Store, run_id: str, cfg: RunConfig) -> list[str]:
    _require_staged(store, run_id)
    return review_actions(store, run_id, cfg) if store.run(run_id).stage_id == "S4" else ["ADVANCE"]


def jev_state(store: Store, run_id: str, cfg: RunConfig) -> dict:
    run = store.run(run_id)
    events = store.events(run_id)
    cohorts = store._artifact_records(run_id, "cohort", ReviewCohort)
    context, version = store.context(run_id), store.version(run_id)
    return {
        "objective": (
            "Explore, select and produce an independently reviewed Experiment Plan "
            "with explicit assumptions, limitations "
            "and downstream actions. Qualification assesses scientific plan quality, not "
            "successful downstream execution. Do not repeat work solely to close "
            "scientific uncertainties. Preserve STOP for technical failures, integrity problems "
            "or missing intent that prevents a meaningful draft."
        ),
        "binding": store.binding(run_id).model_dump(),
        "stage": run.stage_id,
        "research": {
            "question": context.package.research_question,
            "unknowns": context.unknowns,
        }
        if context
        else None,
        "draft": {
            "estimand": version.design.claim.estimand.model_dump(mode="json"),
            "model": version.design.analysis.model,
        }
        if version
        else None,
        "progress": {
            "reviewed_version": run.reviewed_version,
            "reviews_done": run.reviews_done,
            "max_reviews": cfg.max_reviews,
            "revisions_done": run.revisions_done,
            "max_revisions": cfg.max_revisions,
            "cohorts": [
                {
                    "id": c.id,
                    "binding": c.binding.model_dump(),
                    "published": c.published_event_id is not None,
                }
                for c in cohorts[-2:]
            ],
            "recent_actions": [
                {"point": e.data.get("point", e.data.get("scope")), "choice": e.data["choice"]}
                for e in events
                if e.type == "decision"
            ][-5:],
            "last_stop": next((e.data for e in reversed(events) if e.type == "budget.stop"), None),
        },
        "calculations": [
            {"scenario": c.scenario_id, "status": c.status, "reasons": c.reasons}
            for c in (
                store.publication_snapshot(run_id).calculations if store.version(run_id) else []
            )
        ],
        "issues": [
            {
                "id": i.id,
                "severity": i.severity,
                "status": i.status,
                "paths": i.paths,
                "summary": i.mechanism[:180],
                "consequence": i.consequence[:200],
                "basis": i.basis,
            }
            for i in store.issues(run_id)
            if i.status not in {"resolved", "duplicate"}
        ],
        "decisions": [
            {
                "id": d.id,
                "status": d.status,
                "verification": d.verification.get("status"),
                "problem": d.problem[:200],
            }
            for d in store.decisions(run_id)
            if d.purpose == "scientific"
            and (d.status != "APPLIED" or d.verification.get("status") != "verified")
        ],
        "budget": {
            "used": store.usage(run_id).llm_tokens,
            "limit": cfg.llm_tokens,
            "actions": run.actions_done,
            "max_actions": cfg.max_actions,
        },
    }


def run_stage(run_id: str, stage_id: StageId, deps: Deps) -> StageResult:
    _require_staged(deps.store, run_id)
    from experiment_design.stages import (
        framing,
        intake,
        publication,
        qualification,
        researcher_review,
        review,
        synthesis,
    )

    owner = {
        "S1": intake,
        "S2": framing,
        "S3": synthesis,
        "S4": review,
        "S5": qualification,
        "S6": researcher_review,
        "S7": publication,
    }[stage_id]
    return owner.run(run_id, deps)


def advance(run_id: str, deps: Deps) -> RunStatus:
    _require_staged(deps.store, run_id)
    with deps.store.run_execution(run_id):
        return _advance(run_id, deps)


def _advance(run_id: str, deps: Deps) -> RunStatus:
    store = deps.store
    _require_staged(store, run_id)
    if store.run(run_id).stage_id == "S7" and not any(
        d.status == "ANSWERED" for d in store.decisions(run_id)
    ):
        return store.run(run_id).status
    store.update_run(run_id, status="RUNNING")
    try:
        for decision in store.decisions(run_id):
            if decision.purpose == "final_review" and decision.status == "ANSWERED":
                response = decision.responses[-1]
                store.check_binding(run_id, decision.binding)
                if response.action == "CLARIFY":
                    snapshot = store.publication_snapshot(run_id)
                    result = run_specialist(
                        deps,
                        run_id,
                        design,
                        mode="clarification",
                        decision=decision.model_dump(mode="json"),
                        assessment=snapshot.assessment.model_dump(mode="json")
                        if snapshot.assessment
                        else None,
                        calculations=[c.model_dump(mode="json") for c in snapshot.calculations],
                    )
                    with store.transaction():
                        store.check_binding(run_id, decision.binding)
                        answers = dict(decision.interpretation.get("clarifications", {}))
                        answers[response.response_id] = result.findings_or_changes
                        decision.interpretation = {
                            **decision.interpretation,
                            "clarifications": answers,
                        }
                        decision.status = "AWAITING_RESEARCHER"
                        store.save_decision(decision)
                if response.action == "REJECT":
                    decision.status = "APPLIED"
                    decision.application = {
                        "response_id": response.response_id,
                        "outcome": "REJECT",
                    }
                    store.save_decision(decision)
                    store.update_run(run_id, status="INCONCLUSIVE")
                    return "INCONCLUSIVE"
                if response.action == "REQUEST_CHANGES":
                    with store.transaction():
                        issue = store.save_issue(
                            ReviewIssue(
                                run_id=run_id,
                                raised_by="researcher",
                                version_raised=store.version(run_id).version,
                                severity="MAJOR",
                                paths=response.affected_paths or decision.affected_paths or ["/"],
                                mechanism=response.text,
                                consequence="Researcher requests scientific revision",
                                basis="reasoning",
                            )
                        )
                        decision.status = "APPLIED"
                        decision.application = {
                            "response_id": response.response_id,
                            "issue_id": issue.id,
                        }
                        store.save_decision(decision)
                        store.commit_stage(
                            run_id,
                            StageResult(
                                stage_id=store.run(run_id).stage_id,
                                status="REVISION_REQUIRED",
                                binding=store.binding(run_id),
                                output_refs=[decision.id],
                                reason=response.text,
                            ),
                            "S4",
                        )
            if decision.purpose == "scientific" and decision.status == "ANSWERED":
                decision = reconcile_answer(store, run_id, decision)
                if decision.status == "AWAITING_RESEARCHER":
                    continue
                context = store.context(run_id)
                result = run_specialist(
                    deps,
                    run_id,
                    decision_interpretation,
                    decision=decision,
                    allowed_paths=[scientific_path(path) for path in decision.affected_paths],
                    context_patch_allowed=decision.binding.design_version is None,
                )
                interpreted = interpret(decision, context, result.findings_or_changes)
                if interpreted.interpretation.get("ambiguous"):
                    interpreted.revision += 1
                    interpreted.status = "AWAITING_RESEARCHER"
                    store.save_decision(interpreted)
                    store.update_run(run_id, status="NEEDS_RESEARCHER")
                    return "NEEDS_RESEARCHER"
                store.save_decision(interpreted)
                patch = (
                    Patch.model_validate(interpreted.interpretation["patch"])
                    if interpreted.interpretation.get("patch")
                    else None
                )
                apply_decision(store, run_id, decision.id, context=context, patch=patch)
                if store.version(run_id) and store.run(run_id).stage_id != "S2":
                    store.commit_stage(
                        run_id,
                        StageResult(
                            stage_id=store.run(run_id).stage_id,
                            status="REVISION_REQUIRED",
                            binding=store.binding(run_id),
                            output_refs=[decision.id],
                            reason="Researcher intent needs reconciliation",
                        ),
                        "S2",
                    )
        assessment = (
            store.publication_snapshot(run_id).assessment if store.version(run_id) else None
        )
        waiting = [
            d
            for d in store.decisions(run_id)
            if d.status == "AWAITING_RESEARCHER"
            and (
                d.purpose == "scientific"
                or store.run(run_id).stage_id in {"S6", "S7"}
                and assessment is not None
                and d.dependencies == [assessment.id]
                and d.binding.model_dump(exclude={"event_id"})
                == store.binding(run_id).model_dump(exclude={"event_id"})
            )
        ]
        if waiting:
            for decision in waiting:
                if decision.binding.model_dump(exclude={"event_id"}) != store.binding(
                    run_id
                ).model_dump(exclude={"event_id"}):
                    decision.revision += 1
                    decision.binding = store.binding(run_id)
                    store.save_decision(decision)
            status = (
                "REVIEWABLE" if store.run(run_id).stage_id in {"S6", "S7"} else "NEEDS_RESEARCHER"
            )
            store.update_run(run_id, status=status)
            return status
        while store.run(run_id).status == "RUNNING":
            stage = store.run(run_id).stage_id
            result = run_stage(run_id, stage, deps)
            target = "S" + str(int(stage[1]) + 1) if result.status == "COMPLETE" else stage
            if result.status == "REVISION_REQUIRED" and result.issue_refs:
                target = "S4"
            committed = [e for e in store.events(run_id) if e.type == "stage.result"]
            if not committed or any(
                committed[-1].data.get(k) != v for k, v in result.model_dump(mode="json").items()
            ):
                store.commit_stage(run_id, result, target)
            else:
                target = committed[-1].data["target"]
            if result.status == "NEEDS_INPUT":
                store.update_run(
                    run_id, status="REVIEWABLE" if stage == "S6" else "NEEDS_RESEARCHER"
                )
            elif (
                result.status in {"STOPPED", "FAILED"}
                or result.status == "REVISION_REQUIRED"
                and target == stage
            ):
                store.update_run(run_id, status="INCONCLUSIVE")
    except (BudgetStop, JudgeBudgetStop):
        store.update_run(run_id, status="BUDGET_STOP")
    except Exception:
        store.append_event(
            run_id,
            "stage.result",
            StageResult(
                stage_id=store.run(run_id).stage_id,
                status="FAILED",
                binding=store.binding(run_id),
                reason="Stage interrupted; durable artifacts preserved",
            ).model_dump(mode="json"),
        )
        store.update_run(run_id, status="FAILED")
        raise
    return store.run(run_id).status


def request_changes(
    store: Store, run_id: str, text: str, path: str | None = None, *, researcher: str, revision: int
) -> ResearcherDecision:
    _require_staged(store, run_id)
    finals = [
        d
        for d in store.decisions(run_id)
        if d.purpose == "final_review" and d.status == "AWAITING_RESEARCHER"
    ]
    if not finals:
        raise WorkflowError("Change request requires a presented final review decision")
    return respond(
        store,
        run_id,
        finals[-1].id,
        DecisionResponse(
            revision=revision,
            action="REQUEST_CHANGES",
            text=text,
            researcher=researcher,
            affected_paths=[path] if path else [],
            response_id="response:" + uuid4().hex,
        ),
    )


def approve(store: Store, run_id: str, version: int, approver: str):
    _require_staged(store, run_id)
    latest = store.version(run_id)
    if latest is None or version != latest.version:
        raise WorkflowError("Approval requires exact current version")
    finals = [
        d
        for d in store.decisions(run_id)
        if d.purpose == "final_review" and d.status == "AWAITING_RESEARCHER"
    ]
    if not finals:
        raise WorkflowError("Approval requires final review decision")
    decision = finals[-1]
    return approve_version(
        store,
        run_id,
        decision.id,
        DecisionResponse(
            revision=decision.revision,
            action="APPROVE_VERSION",
            researcher=approver,
            response_id="response:" + uuid4().hex,
        ),
    )
