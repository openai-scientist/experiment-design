"""Owned stage progression with scientific coordination confined to review/refinement."""

import hashlib
import json
from dataclasses import dataclass
from uuid import uuid4

from experiment_design.agents import (
    decision_interpretation,
    design,
    handoff,
    methodologist,
    skeptic,
    understanding,
    verifier,
)
from experiment_design.agents.tools import ToolContext, build_tools, source_ids
from experiment_design.config import RunConfig
from experiment_design.contracts import (
    AgentTask,
    DecisionResponse,
    Design,
    Patch,
    ResearchContext,
    ResearcherDecision,
    ReviewCohort,
    ReviewIssue,
    RunStatus,
    SourceSnapshot,
    StageId,
    StageResult,
    TaskBudget,
)
from experiment_design.decisions import apply_decision, interpret, reconcile_answer, respond
from experiment_design.patch import scientific_path
from experiment_design.qualification import approve_version, assess_readiness
from experiment_design.review import (
    commit_revision,
    publish_cohort,
    review_actions,
    review_context,
    start_cohort,
    verification_options,
)
from experiment_design.runtime.jev import (
    Judge,
    JudgeBudgetStop,
    JudgeError,
    Question,
    bounded_decide,
)
from experiment_design.runtime.llm import LLMClient
from experiment_design.runtime.loop import run_agent
from experiment_design.runtime.store import Store
from experiment_design.science.validators import validate_design


@dataclass
class Deps:
    store: Store
    llm: LLMClient
    judge: Judge
    run: RunConfig


class WorkflowError(ValueError):
    pass


class _BudgetStop(RuntimeError):
    pass


def start_run(snapshot: SourceSnapshot, deps: Deps, config_snapshot: dict) -> str:
    return deps.store.create_staged_run(snapshot, config_snapshot)


def _require_staged(store: Store, run_id: str) -> None:
    if store.run(run_id).schema_version != 2:
        raise WorkflowError(
            "schema-1 runs are read/export only; start a new run from original sources"
        )


def legal_actions(store: Store, run_id: str, cfg: RunConfig) -> list[str]:
    _require_staged(store, run_id)
    return review_actions(store, run_id, cfg) if store.run(run_id).stage_id == "S4" else ["ADVANCE"]


def jev_state(store: Store, run_id: str, cfg: RunConfig) -> dict:
    return {
        "binding": store.binding(run_id).model_dump(),
        "stage": store.run(run_id).stage_id,
        "issues": [
            {
                "id": i.id,
                "severity": i.severity,
                "status": i.status,
                "paths": i.paths,
                "summary": i.mechanism[:200],
            }
            for i in store.issues(run_id)
            if i.status not in {"resolved", "duplicate"}
        ],
        "decisions": [
            {"id": d.id, "status": d.status, "problem": d.problem[:200]}
            for d in store.decisions(run_id)
            if d.status != "APPLIED"
        ],
        "budget": {
            "used": store.usage(run_id).llm_tokens,
            "limit": cfg.llm_tokens,
            "actions": store.run(run_id).actions_done,
            "max_actions": cfg.max_actions,
        },
    }


def _choose(
    deps: Deps,
    run_id: str,
    point: str,
    questions: dict[str, Question],
    *,
    proposed_understanding: dict | None = None,
) -> dict[str, str]:
    multi = {k: q for k, q in questions.items() if len(q.options) > 1}
    state = jev_state(deps.store, run_id, deps.run)
    if proposed_understanding is not None:
        state["proposed_understanding"] = proposed_understanding
    decisions = (
        bounded_decide(deps.judge, deps.store, run_id, deps.run, state, multi) if multi else {}
    )
    chosen = {}
    for key, q in questions.items():
        value = decisions[key].choice if key in decisions else next(iter(q.options))
        if value not in q.options:
            raise JudgeError("illegal workflow choice")
        chosen[key] = value
        deps.store.append_event(
            run_id,
            "decision",
            {
                "point": point,
                "choice": value,
                "question": key,
                "by": "jev" if key in decisions else "only_option",
            },
        )
    return chosen


def _budget(deps: Deps, run_id: str) -> int:
    store = deps.store
    remaining = deps.run.llm_tokens - store.usage(run_id).llm_tokens
    reason = (
        "token_limit"
        if remaining <= 0
        else "action_limit"
        if store.run(run_id).actions_done >= deps.run.max_actions
        else None
    )
    if reason:
        store.append_event(
            run_id,
            "budget.stop",
            {
                "scope": "run",
                "reason": reason,
                "tokens_used": store.usage(run_id).llm_tokens,
                "tokens_limit": deps.run.llm_tokens,
                "actions_used": store.run(run_id).actions_done,
                "actions_limit": deps.run.max_actions,
            },
        )
        raise _BudgetStop(reason)
    return remaining


def _context(deps: Deps, run_id: str) -> ToolContext:
    context = deps.store.context(run_id)
    return ToolContext(
        deps.store, run_id, context.package if context else None, deps.store.version(run_id), set()
    )


def _agent(
    deps: Deps,
    run_id: str,
    module,
    ctx: ToolContext | None = None,
    task_id: str | None = None,
    **focus,
):
    ctx = ctx or _context(deps, run_id)
    remaining = _budget(deps, run_id)
    identity = json.dumps(
        {
            "run": run_id,
            "role": module.SPEC.agent_id,
            "stage": deps.store.run(run_id).stage_id,
            "context": ctx.binding.context_revision,
            "version": ctx.binding.design_version,
            "focus": str(focus),
        },
        sort_keys=True,
    )
    task_id = task_id or "task:" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    try:
        task = deps.store.task(task_id).task
        ctx.binding = task.binding
    except KeyError:
        reserve = (
            2 if module in {design, methodologist, skeptic} else 1 if module is verifier else 0
        )
        reserve *= deps.run.task_max_tokens if deps.store.run(run_id).stage_id == "S4" else 0
        available = remaining - reserve
        if available <= 0:
            deps.store.append_event(
                run_id,
                "budget.stop",
                {
                    "scope": "coordination",
                    "reason": "reserved_verification_and_handoff",
                    "tokens_remaining": remaining,
                    "tokens_reserved": reserve,
                },
            )
            raise _BudgetStop("reserved_verification_and_handoff") from None
        task = AgentTask(
            id=task_id,
            binding=ctx.binding,
            stage_id=deps.store.run(run_id).stage_id,
            cohort_id=ctx.cohort_id,
            objective=module.SPEC.system_prompt,
            package_ref=ctx.package.id if ctx.package else ctx.binding.source_snapshot_id,
            design_version=ctx.binding.design_version,
            allowed_tools=list(module.TOOLS),
            issue_refs=[i.id for i in focus.get("issues", focus.get("batch", []))],
            budget=TaskBudget(
                max_turns=deps.run.task_max_turns,
                max_tokens=min(deps.run.task_max_tokens, available),
            ),
            exit_conditions=["Valid structured submission"],
        )
    ctx.task_id = task_id
    result = run_agent(
        module.SPEC,
        task,
        module.brief(ctx, **focus),
        build_tools(ctx),
        module.submit_tool(ctx, **focus),
        deps.llm,
        deps.store,
        run_id,
        ctx.accepted_call_ids,
    )
    if result.completion_status != "completed":
        deps.store.append_event(
            run_id,
            "budget.stop",
            {
                "scope": "task",
                "reason": result.stop_reason,
                "agent_id": module.SPEC.agent_id,
                "tokens_used": result.tokens_in + result.tokens_out,
                "tokens_limit": task.budget.max_tokens,
                "llm_calls": result.llm_calls,
            },
        )
        raise _BudgetStop(result.stop_reason)
    return result


def _save_decisions(store: Store, run_id: str, payload: dict) -> None:
    for data in payload.get("decisions", []):
        decision = ResearcherDecision.model_validate(data)
        if decision.run_id != run_id or decision.responses or decision.application:
            raise WorkflowError("agent cannot author researcher responses")
        decision.binding = store.binding(run_id)
        store.save_decision(decision)


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
        _agent(deps, run_id, module, ctx=ctx, task_id=task_id)
    with deps.store.transaction():
        publish_cohort(deps.store, run_id, cohort.id)
        run = deps.store.run(run_id)
        deps.store.update_run(
            run_id,
            reviewed_version=deps.store.version(run_id).version,
            reviews_done=run.reviews_done + 1,
        )


def _refine(deps: Deps, run_id: str):
    issues = [
        i
        for i in deps.store.issues(run_id)
        if i.status == "open" and i.severity in {"MAJOR", "BLOCKER"}
    ]
    payload = _agent(deps, run_id, design, mode="revision", batch=issues).findings_or_changes
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
        _save_decisions(deps.store, run_id, payload)
        deps.store.update_run(run_id, revisions_done=deps.store.run(run_id).revisions_done + 1)
    if deps.store.version(run_id).design_hash == parent.design_hash and not payload.get(
        "rebuttals"
    ):
        deps.store.append_event(
            run_id, "budget.stop", {"scope": "coordination", "reason": "no_progress"}
        )
        raise _BudgetStop("no_progress")


def _verify(deps: Deps, run_id: str):
    issues = [i for i in deps.store.issues(run_id) if i.status in {"addressed", "rebutted"}]
    result = _agent(deps, run_id, verifier, issues=issues)
    live = validate_design(
        deps.store.version(run_id).design,
        deps.store.context(run_id).package,
        source_ids(_context(deps, run_id)),
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
    outcomes = _choose(deps, run_id, "verification", questions)
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
        if all(i.status == "resolved" for i in issues):
            for decision in deps.store.decisions(run_id):
                if (
                    decision.status == "APPLIED"
                    and decision.purpose != "final_review"
                    and decision.application.get("issue_id") in outcomes
                    and outcomes[decision.application["issue_id"]] == "resolved"
                ):
                    decision.verification = {
                        "status": "verified",
                        "evidence_refs": verdicts[decision.application["issue_id"]][
                            "evidence_refs"
                        ],
                    }
                    deps.store.save_decision(decision)


def run_stage(run_id: str, stage_id: StageId, deps: Deps) -> StageResult:
    store = deps.store
    _require_staged(store, run_id)
    outputs, issues, status, reason = [], [], "COMPLETE", ""
    if stage_id == "S1":
        outputs = [store.source_snapshot(run_id).id]
        reason = "Readable sources captured exactly"
    elif stage_id == "S2":
        payload = _agent(deps, run_id, understanding).findings_or_changes
        pending = [ResearcherDecision.model_validate(d) for d in payload.get("decisions", [])]
        choice = "CONTINUE"
        if pending:
            options = ["CONTINUE", "STOP"]
            if any(d.options or d.dependencies for d in pending):
                options.insert(0, "ASK_RESEARCHER")
            choice = _choose(
                deps,
                run_id,
                "understanding",
                {
                    "next_action": Question(
                        "Continue synthesis or ask at an informative decision or dependency block.",
                        {option: option for option in options},
                    )
                },
                proposed_understanding=payload,
            )["next_action"]
        with store.transaction():
            context = store.save_context(run_id, ResearchContext.model_validate(payload["context"]))
            _save_decisions(store, run_id, payload)
            outputs = [f"context:{context.revision}"]
            reason = "Research understanding with provenance recorded"
            if choice == "ASK_RESEARCHER":
                for proposed in pending:
                    decision = store.decision(run_id, proposed.id)
                    decision.status = "AWAITING_RESEARCHER"
                    store.save_decision(decision)
                status, reason = "NEEDS_INPUT", "Research understanding needs a scientific decision"
            elif choice == "STOP":
                status, reason = (
                    "STOPPED",
                    "Research understanding stopped with uncertainty preserved",
                )
            result = StageResult(
                stage_id="S2",
                status=status,
                binding=store.binding(run_id),
                output_refs=outputs,
                reason=reason,
            )
            store.commit_stage(run_id, result, "S3" if status == "COMPLETE" else "S2")
            return result
    elif stage_id == "S3":
        if store.version(run_id) is None:
            payload = _agent(deps, run_id, design, mode="initial").findings_or_changes
            with store.transaction():
                version = store.add_version(
                    run_id, Design.model_validate(payload["design"]), author="design", parent=None
                )
                _save_decisions(store, run_id, payload)
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
            outputs = [version.design_hash]
        else:
            _refine(deps, run_id)
            outputs = [store.version(run_id).design_hash]
        reason = "Structured draft committed"
    elif stage_id == "S4":
        while True:
            _budget(deps, run_id)
            cohorts = store._artifact_records(run_id, "cohort", ReviewCohort)
            pending_cohort = bool(cohorts and cohorts[-1].published_event_id is None)
            actions = review_actions(store, run_id, deps.run)
            action = (
                "REVIEW"
                if pending_cohort
                else _choose(
                    deps,
                    run_id,
                    "next_action",
                    {
                        "next_action": Question(
                            "Choose useful scientific work; qualify after material verification.",
                            {a: a for a in actions},
                        )
                    },
                )["next_action"]
            )
            if action == "QUALIFY":
                outputs, reason = (
                    [store.version(run_id).design_hash],
                    "Review permits independent qualification",
                )
                break
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
            {"REVIEW": _cohort, "REVISE": _refine, "VERIFY": _verify}[action](deps, run_id)
            store.update_run(run_id, actions_done=store.run(run_id).actions_done + 1)
    elif stage_id == "S5":
        inspections = {
            e.task_id
            for e in store.events(run_id)
            if e.type == "task.started" and e.agent_id == "handoff"
        }
        checkpoint = {
            "issues": [i.model_dump(mode="json") for i in store.issues(run_id)],
            "decisions": [
                d.model_dump(mode="json")
                for d in store.decisions(run_id)
                if d.purpose != "final_review"
            ],
            "evidence_events": [
                e.event_id
                for e in store.events(run_id)
                if e.type == "artifact"
                and e.data["kind"] == "observation"
                and e.data["record"]["task_id"] not in inspections
            ],
        }
        result = _agent(deps, run_id, handoff, support_checkpoint=checkpoint)
        with store.transaction():
            assessment = assess_readiness(store, run_id, result)
            for decision_id in assessment.decision_ids:
                decision = store.decision(run_id, decision_id)
                if decision.status in {"IDENTIFIED", "EXPLORING"}:
                    decision.binding = store.binding(run_id)
                    decision.status = "AWAITING_RESEARCHER"
                    store.save_decision(decision)
            if assessment.decision_ids:
                assessment.binding = store.binding(run_id)
                store.save_assessment(assessment)
            outputs = [assessment.id]
            issues = [
                i.id
                for i in store.issues(run_id)
                if i.status == "open" and i.severity in {"MAJOR", "BLOCKER"}
            ]
            status = (
                "NEEDS_INPUT"
                if assessment.review_status == "NEEDS_INPUT"
                else "REVISION_REQUIRED"
                if assessment.review_status == "NOT_READY"
                else "COMPLETE"
            )
            reason = "; ".join(assessment.blockers) or "Independent handoff inspection complete"
            result = StageResult(
                stage_id="S5",
                status=status,
                binding=store.binding(run_id),
                output_refs=outputs,
                issue_refs=issues,
                reason=reason,
            )
            target = (
                "S6"
                if status == "COMPLETE"
                else "S4"
                if status == "REVISION_REQUIRED" and issues
                else "S5"
            )
            store.commit_stage(run_id, result, target)
            return result
    elif stage_id == "S6":
        finals = [
            d
            for d in store.decisions(run_id)
            if d.purpose == "final_review"
            and d.status == "AWAITING_RESEARCHER"
            and d.binding.model_dump(exclude={"event_id"})
            == store.binding(run_id).model_dump(exclude={"event_id"})
            and d.dependencies == [store.assessment(run_id).id]
        ]
        if not finals:
            finals = [
                store.save_decision(
                    ResearcherDecision(
                        id="final:" + uuid4().hex,
                        run_id=run_id,
                        purpose="final_review",
                        status="AWAITING_RESEARCHER",
                        binding=store.binding(run_id),
                        problem="Review this exact design and its handoff assessment",
                        dependencies=[store.assessment(run_id).id],
                    )
                )
            ]
        outputs, status, reason = (
            [finals[-1].id],
            "NEEDS_INPUT",
            "Waiting for final researcher review",
        )
    else:
        status, reason = "NEEDS_INPUT", "Publication needs explicit output destination"
    return StageResult(
        stage_id=stage_id,
        status=status,
        binding=store.binding(run_id),
        output_refs=outputs,
        issue_refs=issues,
        reason=reason,
    )


def advance(run_id: str, deps: Deps) -> RunStatus:
    store = deps.store
    _require_staged(store, run_id)
    if store.run(run_id).stage_id == "S7":
        return store.run(run_id).status
    store.update_run(run_id, status="RUNNING")
    try:
        for decision in store.decisions(run_id):
            if decision.purpose == "final_review" and decision.status == "ANSWERED":
                response = decision.responses[-1]
                store.check_binding(run_id, decision.binding)
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
                result = _agent(
                    deps,
                    run_id,
                    decision_interpretation,
                    decision=decision,
                    allowed_paths=[scientific_path(path) for path in decision.affected_paths],
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
        waiting = [d for d in store.decisions(run_id) if d.status == "AWAITING_RESEARCHER"]
        if waiting:
            for decision in waiting:
                if decision.binding.model_dump(exclude={"event_id"}) != store.binding(
                    run_id
                ).model_dump(exclude={"event_id"}):
                    decision.revision += 1
                    decision.binding = store.binding(run_id)
                    store.save_decision(decision)
            status = "REVIEWABLE" if store.run(run_id).stage_id == "S6" else "NEEDS_RESEARCHER"
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
    except (_BudgetStop, JudgeBudgetStop):
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


def answer_request(store: Store, run_id: str, request_id: str, answer: str) -> None:
    raise WorkflowError("Use decide with a ResearcherDecision revision; legacy answer is read-only")


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
