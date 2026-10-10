"""JEV selects scientific workflow actions; code validates and commits them atomically."""

import json
import math
from dataclasses import dataclass

from experiment_design.agents import design, methodologist, skeptic, verifier
from experiment_design.agents.tools import ToolContext, build_tools, source_ids
from experiment_design.config import RunConfig
from experiment_design.contracts import (
    CLAIM_FIELDS,
    AgentTask,
    Approval,
    Finding,
    Patch,
    ResearcherRequest,
    ReviewIssue,
    RunStatus,
    ScientificPackage,
    TaskBudget,
)
from experiment_design.patch import apply_patch, resolve, set_claim
from experiment_design.runtime.jev import Decision, Judge, JudgeError, Question
from experiment_design.runtime.llm import LLMClient
from experiment_design.runtime.loop import run_agent
from experiment_design.runtime.store import Store, now
from experiment_design.science.validators import validate_design

ACTIONS = ("REVISE", "REVIEW", "VERIFY", "ASK_RESEARCHER", "MARK_REVIEWABLE", "STOP")
ACTION_DESCRIPTIONS = {
    "REVISE": "Address related material concerns with a patch or evidence-backed rebuttal.",
    "REVIEW": "Review the latest unreviewed version with the methodologist and skeptic.",
    "VERIFY": "Independently verify addressed concerns and rebuttals, checking for regressions.",
    "ASK_RESEARCHER": "Open draft questions and wait for researcher decisions or evidence.",
    "MARK_REVIEWABLE": "Present the reviewed design for researcher assessment and approval.",
    "STOP": "Stop honestly when further work is not justified; report remaining blockers.",
}


@dataclass
class Deps:
    store: Store
    llm: LLMClient
    judge: Judge
    run: RunConfig


class WorkflowError(ValueError):
    pass


class _TaskExhausted(RuntimeError):
    def __init__(self, **details):
        self.details = details
        super().__init__(details["reason"])


def start_run(package: ScientificPackage, deps: Deps, config_snapshot: dict) -> str:
    return deps.store.create_run(package, config_snapshot)


def _material(issue: ReviewIssue) -> bool:
    return issue.status == "open" and (
        issue.severity in {"MAJOR", "BLOCKER"} or issue.raised_by == "researcher"
    )


def legal_actions(store: Store, run_id: str, cfg: RunConfig) -> list[str]:
    run = store.run(run_id)
    version = store.version(run_id)
    issues = store.issues(run_id)
    blockers = any(i.severity == "BLOCKER" and i.status in {"open", "unresolved"} for i in issues)
    pending_verify = any(i.status in {"addressed", "rebutted"} for i in issues)
    researcher_pending = any(i.raised_by == "researcher" and i.status == "open" for i in issues)
    legal = {
        "REVIEW": version is not None
        and version.version > run.reviewed_version
        and run.reviews_done < cfg.max_reviews,
        "REVISE": any(_material(i) for i in issues) and run.revisions_done < cfg.max_revisions,
        "VERIFY": pending_verify,
        "ASK_RESEARCHER": any(r.status == "draft" for r in store.requests(run_id)),
        "MARK_REVIEWABLE": run.reviewed_version >= 1
        and not pending_verify
        and not blockers
        and not researcher_pending,
        "STOP": True,
    }
    return [action for action in ACTIONS if legal[action]]


def jev_state(store: Store, run_id: str, cfg: RunConfig) -> dict:
    run, version, usage = store.run(run_id), store.version(run_id), store.usage(run_id)
    history = [e for e in store.events(run_id) if e.type == "decision"][-6:]
    return {
        "research_question": run.package.research_question,
        "version": version.version if version else 0,
        "reviewed_version": run.reviewed_version,
        "budget": {
            "tokens_used": usage.llm_tokens,
            "tokens_limit": cfg.llm_tokens,
            "actions_used": run.actions_done,
            "max_actions": cfg.max_actions,
            "reviews_done": run.reviews_done,
            "revisions_done": run.revisions_done,
        },
        "issues": [
            {
                "id": i.id,
                "severity": i.severity,
                "status": i.status,
                "raised_by": i.raised_by,
                "paths": i.paths,
                "summary": i.mechanism[:200],
            }
            for i in store.issues(run_id)
            if i.status not in {"resolved", "duplicate"}
        ],
        "requests": [
            {
                "id": r.id,
                "kind": r.kind,
                "question": r.question[:200],
            }
            for r in store.requests(run_id)
            if r.status in {"draft", "open"}
        ],
        "history": [{"point": e.data["point"], "choice": e.data["choice"]} for e in history],
    }


def _decide(deps: Deps, run_id: str, point: str, questions: dict[str, Question]) -> dict[str, str]:
    multi = {key: q for key, q in questions.items() if len(q.options) > 1}
    decisions = deps.judge.decide(jev_state(deps.store, run_id, deps.run), multi) if multi else {}
    choices = {}
    for key, question in questions.items():
        if not question.options:
            raise JudgeError(f"No legal options for {key}")
        if key in multi:
            if key not in decisions:
                raise JudgeError(f"Missing decision {key}")
            decision, by = decisions[key], "jev"
        else:
            choice = next(iter(question.options))
            decision, by = Decision(choice, {choice: 1.0}, 1.0), "only_option"
        if decision.choice not in question.options:
            raise JudgeError(f"Illegal choice {decision.choice} for {key}")
        choices[key] = decision.choice
        deps.store.append_event(
            run_id,
            "decision",
            {
                "point": point,
                "question": key,
                "options": list(question.options),
                "choice": decision.choice,
                "probabilities": decision.probabilities,
                "confidence": decision.confidence,
                "by": by,
                "latency_ms": decision.latency_ms,
                "tokens": decision.tokens,
            },
        )
    return choices


def _context(deps: Deps, run_id: str) -> ToolContext:
    return ToolContext(
        deps.store,
        run_id,
        deps.store.run(run_id).package,
        deps.store.version(run_id),
        set(),
    )


def _agent(deps: Deps, ctx: ToolContext, module, **focus) -> dict:
    remaining = deps.run.llm_tokens - deps.store.usage(ctx.run_id).llm_tokens
    if remaining <= 0:
        raise _TaskExhausted(
            scope="run",
            reason="token_limit",
            tokens_used=deps.store.usage(ctx.run_id).llm_tokens,
            tokens_limit=deps.run.llm_tokens,
        )
    ctx.accepted_call_ids.clear()
    task = AgentTask(
        objective=module.SPEC.system_prompt,
        package_ref=ctx.package.id,
        design_version=ctx.version.version if ctx.version else None,
        focus_paths=[
            p
            for i in focus.get("batch", focus.get("issues", []))
            if hasattr(i, "paths")
            for p in i.paths
        ],
        issue_refs=[i.id for i in focus.get("batch", focus.get("issues", [])) if hasattr(i, "id")],
        allowed_tools=list(module.TOOLS),
        budget=TaskBudget(
            max_turns=deps.run.task_max_turns,
            max_tokens=min(deps.run.task_max_tokens, remaining),
        ),
        exit_conditions=["Submit a valid task result or exhaust the task budget"],
    )
    result = run_agent(
        module.SPEC,
        task,
        module.brief(ctx, **focus),
        build_tools(ctx),
        module.submit_tool(ctx, **focus),
        deps.llm,
        deps.store,
        ctx.run_id,
        ctx.accepted_call_ids,
    )
    if result.completion_status != "completed":
        run_tokens = deps.store.usage(ctx.run_id).llm_tokens
        run_exhausted = result.stop_reason == "token_limit" and run_tokens >= deps.run.llm_tokens
        task_tokens = result.tokens_in + result.tokens_out
        raise _TaskExhausted(
            scope="run" if run_exhausted else "task",
            reason=result.stop_reason,
            agent_id=module.SPEC.agent_id,
            tokens_used=run_tokens if run_exhausted else task_tokens,
            tokens_limit=deps.run.llm_tokens if run_exhausted else task.budget.max_tokens,
            task_tokens_used=task_tokens,
            task_tokens_limit=task.budget.max_tokens,
            llm_calls=result.llm_calls,
            max_turns=task.budget.max_turns,
        )
    return result.findings_or_changes


def _validate_new(deps: Deps, run_id: str) -> None:
    ctx = _context(deps, run_id)
    active = {
        (i.validator_code, p)
        for i in deps.store.issues(run_id)
        if i.status in {"open", "addressed", "rebutted", "unresolved"}
        for p in i.paths
    }
    for f in validate_design(ctx.version.design, ctx.package, source_ids(ctx)):
        if (f.code, f.path) not in active:
            deps.store.save_issue(
                ReviewIssue(
                    run_id=run_id,
                    raised_by="validator",
                    version_raised=ctx.version.version,
                    severity=f.severity,
                    paths=[f.path],
                    mechanism=f.message,
                    consequence=f.consequence,
                    basis="computation",
                    validator_code=f.code,
                )
            )


def _save_requests(store: Store, payload: dict) -> None:
    for request in payload.get("requests", []):
        store.save_request(ResearcherRequest.model_validate(request))


def _finish_action(deps: Deps, run_id: str, **fields) -> None:
    deps.store.update_run(
        run_id,
        actions_done=deps.store.run(run_id).actions_done + 1,
        **fields,
    )


def _duplicates(deps: Deps, run_id: str, findings: list[tuple[Finding, str]]) -> dict[str, str]:
    questions = {
        str(index): Question(
            f"Is this finding materially distinct from issue {f.duplicate_of}? "
            + json.dumps(f.model_dump(mode="json")),
            {
                "distinct": "A distinct material concern",
                "duplicate": "The same concern as the cited issue",
            },
        )
        for index, (f, _) in enumerate(findings)
        if f.duplicate_of
    }
    return _decide(deps, run_id, "issue_disposition", questions) if questions else {}


def _save_findings(deps: Deps, run_id: str, findings, dispositions) -> None:
    version = deps.store.version(run_id).version
    for index, (finding, raised_by) in enumerate(findings):
        data = finding.model_dump(mode="json")
        duplicate = dispositions.get(str(index)) == "duplicate"
        if not duplicate:
            data["duplicate_of"] = None
        deps.store.save_issue(
            ReviewIssue(
                **data,
                run_id=run_id,
                raised_by=raised_by,
                version_raised=version,
                status="duplicate" if duplicate else "open",
            )
        )


def _review(deps: Deps, run_id: str) -> None:
    ctx = _context(deps, run_id)
    method = _agent(deps, ctx, methodologist)
    ctx.pending_findings = [Finding.model_validate(f) for f in method["findings"]]
    stress = _agent(deps, ctx, skeptic)
    findings = [(f, "methodologist") for f in ctx.pending_findings] + [
        (Finding.model_validate(f), "skeptic") for f in stress["findings"]
    ]
    dispositions = _duplicates(deps, run_id, findings)
    with deps.store.transaction():
        _save_findings(deps, run_id, findings, dispositions)
        _finish_action(
            deps,
            run_id,
            reviewed_version=ctx.version.version,
            reviews_done=deps.store.run(run_id).reviews_done + 1,
        )


def _revise(deps: Deps, run_id: str) -> None:
    ctx = _context(deps, run_id)
    batches = {}
    for issue in deps.store.issues(run_id):
        if _material(issue):
            root = issue.paths[0].strip("/").split("/")[0] or "root"
            batches.setdefault(root, []).append(issue)
    root = _decide(
        deps,
        run_id,
        "revision_batch",
        {
            "batch": Question(
                "Choose one related batch to address together",
                {
                    name: "; ".join(f"{i.id}: {i.mechanism[:200]}" for i in issues)
                    for name, issues in batches.items()
                },
            ),
        },
    )["batch"]
    batch = batches[root]
    payload = _agent(deps, ctx, design, mode="revise", batch=batch)
    patch = Patch.model_validate(payload["patch"])
    proposed = apply_patch(ctx.version.design, patch, source_ids=source_ids(ctx))
    parent_findings = {
        (f.code, f.path) for f in validate_design(ctx.version.design, ctx.package, source_ids(ctx))
    }
    new_findings = [
        f
        for f in validate_design(proposed, ctx.package, source_ids(ctx))
        if f.severity in {"BLOCKER", "MAJOR"} and (f.code, f.path) not in parent_findings
    ]
    revisions = 1
    if new_findings:
        outcome = _decide(
            deps,
            run_id,
            "patch_outcome",
            {
                "patch_outcome": Question(
                    "The patch introduced material findings. Choose keep, repair, or revert: "
                    + json.dumps([f.model_dump(mode="json") for f in new_findings]),
                    {
                        "keep": "Keep the change and report the new concerns",
                        "repair": "Make one further targeted repair before committing",
                        "revert": "Discard the proposed design and leave batch issues open",
                    },
                ),
            },
        )["patch_outcome"]
        if outcome == "revert":
            with deps.store.transaction():
                _finish_action(
                    deps, run_id, revisions_done=deps.store.run(run_id).revisions_done + 1
                )
            return
        if outcome == "repair":
            repair_issues = [
                ReviewIssue(
                    id=f"repair:{index}",
                    run_id=run_id,
                    raised_by="validator",
                    version_raised=ctx.version.version + 1,
                    severity=f.severity,
                    paths=[f.path],
                    mechanism=f.message,
                    consequence=f.consequence,
                    basis="computation",
                    validator_code=f.code,
                )
                for index, f in enumerate(new_findings)
            ]
            ctx.version = ctx.version.model_copy(update={"design": proposed})
            repair = _agent(deps, ctx, design, mode="revise", batch=repair_issues)
            repair_patch = Patch.model_validate(repair["patch"])
            proposed = apply_patch(proposed, repair_patch, source_ids=source_ids(ctx))
            patch = Patch(
                ops=patch.ops + repair_patch.ops,
                rationale=patch.rationale + "; repair: " + repair_patch.rationale,
                addresses=patch.addresses,
            )
            payload["requests"] += repair["requests"]
            revisions += 1
    with deps.store.transaction():
        parent = deps.store.version(run_id)
        new_version = deps.store.add_version(
            run_id,
            proposed,
            author="design",
            parent=parent.version,
            patch=patch,
        )
        rebuttals = {r["issue_id"]: r for r in payload["rebuttals"]}
        for issue in batch:
            if issue.id in patch.addresses:
                issue.status, issue.addressed_in = "addressed", new_version.version
            else:
                issue.status, issue.rebuttal = "rebutted", rebuttals[issue.id]["argument"]
                issue.evidence_refs = list(
                    dict.fromkeys(
                        issue.evidence_refs + rebuttals[issue.id]["evidence_refs"],
                    )
                )
            deps.store.save_issue(issue)
        _save_requests(deps.store, payload)
        _validate_new(deps, run_id)
        _finish_action(
            deps,
            run_id,
            revisions_done=deps.store.run(run_id).revisions_done + revisions,
        )


def _verify(deps: Deps, run_id: str) -> None:
    ctx = _context(deps, run_id)
    issues = [i for i in deps.store.issues(run_id) if i.status in {"addressed", "rebutted"}]
    payload = _agent(deps, ctx, verifier, issues=issues)
    verdicts = {v["issue_id"]: v for v in payload["verdicts"]}
    live = {
        (f.code, f.path)
        for f in validate_design(
            ctx.version.design,
            ctx.package,
            source_ids(ctx),
        )
    }
    questions = {}
    for issue in issues:
        options = {
            "resolved": "The concern is independently resolved",
            "reopen": "Further correction is needed",
            "uncertain": "Evidence is inconclusive",
        }
        if issue.validator_code and any((issue.validator_code, p) in live for p in issue.paths):
            del options["resolved"]
        questions[issue.id] = Question(
            "Choose the effective verification outcome. Verifier recommendation: "
            + json.dumps(verdicts[issue.id]),
            options,
        )
    outcomes = _decide(deps, run_id, "verification", questions)
    regressions = [(Finding.model_validate(f), "verifier") for f in payload["regressions"]]
    dispositions = _duplicates(deps, run_id, regressions)
    with deps.store.transaction():
        for issue in issues:
            outcome = outcomes[issue.id]
            issue.status = {"resolved": "resolved", "reopen": "open", "uncertain": "unresolved"}[
                outcome
            ]
            deps.store.save_issue(issue)
            deps.store.append_event(
                run_id,
                "verification",
                {
                    "issue_id": issue.id,
                    "verdict": verdicts[issue.id]["verdict"],
                    "grounds": verdicts[issue.id]["grounds"],
                    "evidence_refs": verdicts[issue.id]["evidence_refs"],
                    "effective_outcome": outcome,
                },
                agent_id="verifier",
                design_version=ctx.version.version,
            )
        _save_findings(deps, run_id, regressions, dispositions)
        _finish_action(deps, run_id)


def _ask_researcher(deps: Deps, run_id: str) -> None:
    with deps.store.transaction():
        for request in deps.store.requests(run_id):
            if request.status == "draft":
                request.status = "open"
                deps.store.save_request(request)
        _finish_action(deps, run_id, status="NEEDS_RESEARCHER")


def _mark_reviewable(deps: Deps, run_id: str) -> None:
    with deps.store.transaction():
        _finish_action(deps, run_id, status="REVIEWABLE")


def _stop(deps: Deps, run_id: str) -> None:
    blocked = any(
        i.severity == "BLOCKER" and i.status in {"open", "unresolved"}
        for i in deps.store.issues(run_id)
    )
    with deps.store.transaction():
        _finish_action(deps, run_id, status="BLOCKED" if blocked else "INCONCLUSIVE")


def _answer_value(request: ResearcherRequest, answer: str) -> str | float:
    if not answer.strip():
        raise WorkflowError("Researcher answer must be non-empty")
    if request.kind != "decision":
        return answer
    if request.path not in {f"/claim/{field}" for field in CLAIM_FIELDS}:
        raise WorkflowError(f"Invalid decision claim path {request.path}")
    field = request.path.split("/")[-1]
    if field == "margin":
        try:
            value = float(answer)
            if not math.isfinite(value):
                raise ValueError("not finite")
            return value
        except ValueError as exc:
            raise WorkflowError("margin answer must be a finite number") from exc
    choices = {
        "contrast_type": {"superiority", "non_inferiority", "superiority_by_margin"},
        "direction": {"increase", "decrease"},
        "outcome_kind": {"continuous", "binary"},
    }
    if field in choices and answer not in choices[field]:
        raise WorkflowError(f"{field} must be one of {', '.join(sorted(choices[field]))}")
    return answer


def _apply_answers(deps: Deps, run_id: str) -> None:
    pending = [
        r
        for r in deps.store.requests(run_id)
        if r.kind == "decision" and r.status == "answered" and r.applied_in is None
    ]
    if not pending:
        return
    parent = deps.store.version(run_id)
    d = parent.design
    changed = []
    for request in pending:
        field = request.path.split("/")[-1]
        value = _answer_value(request, request.answer)
        if getattr(d.claim, field).value != value:
            changed.append(request)
        d = set_claim(d, field, value, "researcher")
    with deps.store.transaction():
        version = deps.store.add_version(run_id, d, author="researcher", parent=parent.version)
        for request in pending:
            request.applied_in = version.version
            deps.store.save_request(request)
        for request in changed:
            deps.store.save_issue(
                ReviewIssue(
                    run_id=run_id,
                    raised_by="researcher",
                    version_raised=version.version,
                    severity="MAJOR",
                    paths=["/"],
                    basis="reasoning",
                    mechanism=(
                        f"Reconcile the design with researcher decision {request.path}: "
                        f"{request.answer}"
                    ),
                    consequence=(
                        "Dependent measurements, analysis and decision rules may still "
                        "express the earlier claim"
                    ),
                )
            )
        _validate_new(deps, run_id)


def advance(run_id: str, deps: Deps) -> RunStatus:
    store = deps.store
    if store.run(run_id).status == "NEEDS_RESEARCHER" and any(
        r.status == "open" for r in store.requests(run_id)
    ):
        return "NEEDS_RESEARCHER"
    store.update_run(run_id, status="RUNNING")
    _apply_answers(deps, run_id)
    handlers = {
        "REVIEW": _review,
        "REVISE": _revise,
        "VERIFY": _verify,
        "ASK_RESEARCHER": _ask_researcher,
        "MARK_REVIEWABLE": _mark_reviewable,
        "STOP": _stop,
    }
    try:
        if store.version(run_id) is None:
            ctx = _context(deps, run_id)
            payload = _agent(deps, ctx, design, mode="initial")
            from experiment_design.contracts import Design

            with store.transaction():
                store.add_version(
                    run_id, Design.model_validate(payload["design"]), author="design", parent=None
                )
                _save_requests(store, payload)
                _validate_new(deps, run_id)
        while store.run(run_id).status == "RUNNING":
            if store.usage(run_id).llm_tokens >= deps.run.llm_tokens:
                raise _TaskExhausted(
                    scope="run",
                    reason="token_limit",
                    tokens_used=store.usage(run_id).llm_tokens,
                    tokens_limit=deps.run.llm_tokens,
                )
            if store.run(run_id).actions_done >= deps.run.max_actions:
                raise _TaskExhausted(
                    scope="run",
                    reason="action_limit",
                    actions_used=store.run(run_id).actions_done,
                    actions_limit=deps.run.max_actions,
                )
            action = _decide(
                deps,
                run_id,
                "next_action",
                {
                    "next_action": Question(
                        "Choose the next useful legal action. A good initial design may need no "
                        "revision. Prioritize material concerns and researcher input.",
                        {a: ACTION_DESCRIPTIONS[a] for a in legal_actions(store, run_id, deps.run)},
                    ),
                },
            )["next_action"]
            handlers[action](deps, run_id)
    except _TaskExhausted as exc:
        with store.transaction():
            store.append_event(run_id, "budget.stop", exc.details)
            store.update_run(run_id, status="BUDGET_STOP")
    return store.run(run_id).status


def answer_request(store: Store, run_id: str, request_id: str, answer: str) -> None:
    requests = {r.id: r for r in store.requests(run_id)}
    if request_id not in requests:
        raise WorkflowError(f"unknown request {request_id}")
    request = requests[request_id]
    if request.status == "answered":
        raise WorkflowError(f"request {request_id} is already answered")
    if store.run(run_id).status != "NEEDS_RESEARCHER" or request.status != "open":
        raise WorkflowError(f"request {request_id} is not open for researcher input")
    _answer_value(request, answer)
    request.status, request.answer = "answered", answer
    with store.transaction():
        store.save_request(request)
        version = store.version(run_id)
        if request.kind == "input" and version is not None:
            store.save_issue(
                ReviewIssue(
                    run_id=run_id,
                    raised_by="researcher",
                    version_raised=version.version,
                    severity="MAJOR",
                    paths=[request.path or "/"],
                    basis="reasoning",
                    mechanism=f"Incorporate researcher input: {request.question}",
                    consequence=(
                        "The current design may use assumptions superseded by the supplied evidence"
                    ),
                    evidence_refs=[f"researcher:{request.id}"],
                )
            )


def request_changes(store: Store, run_id: str, text: str, path: str | None = None) -> ReviewIssue:
    version = store.version(run_id)
    if version is None or not text.strip():
        raise WorkflowError("A design and non-empty change request are required")
    try:
        resolve(version.design, path or "/")
    except ValueError as exc:
        raise WorkflowError(str(exc)) from exc
    with store.transaction():
        issue = store.save_issue(
            ReviewIssue(
                run_id=run_id,
                raised_by="researcher",
                version_raised=version.version,
                severity="MAJOR",
                paths=[path or "/"],
                mechanism=text,
                consequence="Researcher cannot accept this design until the concern is addressed",
                basis="reasoning",
            )
        )
        if store.run(run_id).status in {"REVIEWABLE", "BLOCKED", "INCONCLUSIVE", "BUDGET_STOP"}:
            store.update_run(run_id, status="RUNNING")
    return issue


def approve(store: Store, run_id: str, version: int, approver: str) -> Approval:
    latest = store.version(run_id)
    if store.run(run_id).status != "REVIEWABLE" or latest is None or version != latest.version:
        raise WorkflowError("Approval requires REVIEWABLE status and the latest version")
    if not approver.strip():
        raise WorkflowError("Approver name must be non-empty")
    approval = Approval(
        run_id=run_id,
        version=version,
        design_hash=latest.design_hash,
        approver=approver,
        at=now(),
    )
    store.add_approval(approval)
    return approval
