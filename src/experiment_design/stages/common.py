"""Owned stage progression with scientific coordination confined to review/refinement."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from experiment_design.agents import (
    design,
    explorer,
    methodologist,
    skeptic,
    verifier,
)
from experiment_design.agents.tools import ToolContext, build_tools
from experiment_design.config import RunConfig
from experiment_design.contracts import (
    AgentTask,
    Observation,
    ResearcherDecision,
    TaskBudget,
)
from experiment_design.runtime.jev import (
    JudgeError,
    Question,
    bounded_decide,
)
from experiment_design.runtime.loop import run_agent

if TYPE_CHECKING:
    from experiment_design.runtime.store import Store
    from experiment_design.workflow import Deps


class BudgetStop(RuntimeError):
    pass


def choose_next_action(
    deps: Deps,
    run_id: str,
    point: str,
    questions: dict[str, Question],
    *,
    proposed_understanding: dict | None = None,
) -> dict[str, str]:
    from experiment_design.workflow import jev_state

    multi = {k: q for k, q in questions.items() if len(q.options) > 1}
    state = jev_state(deps.store, run_id, deps.run)
    if proposed_understanding is not None:
        state["proposed_understanding"] = proposed_understanding
        state["sources"] = deps.store.source_snapshot(run_id).model_dump(mode="json")
        state["observations"] = [
            o.model_dump(mode="json")
            for o in deps.store._artifact_records(run_id, "observation", Observation)
            if o.accepted
            and o.task_id.startswith("explore:")
            and o.binding.model_dump(exclude={"event_id"})
            == deps.store.binding(run_id).model_dump(exclude={"event_id"})
        ]
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


def remaining_budget(deps: Deps, run_id: str) -> int:
    store = deps.store
    deps.run = RunConfig.model_validate(store.run(run_id).config.get("run", deps.run.model_dump()))
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
        raise BudgetStop(reason)
    return remaining


def tool_context(deps: Deps, run_id: str) -> ToolContext:
    context = deps.store.context(run_id)
    return ToolContext(
        deps.store, run_id, context.package if context else None, deps.store.version(run_id), set()
    )


def run_specialist(
    deps: Deps,
    run_id: str,
    module,
    ctx: ToolContext | None = None,
    task_id: str | None = None,
    **focus,
):
    ctx = ctx or tool_context(deps, run_id)
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
    record = None
    try:
        record = deps.store.recover_task(run_id, task_id)
        task = record.task
        ctx.binding = task.binding
        deps.store.check_binding(run_id, task.binding)
        if record.submission is not None:
            return record.submission
    except KeyError:
        record = None
    remaining = remaining_budget(deps, run_id)
    if record is None:
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
                    "reason": "reserved_independent_review",
                    "tokens_remaining": remaining,
                    "tokens_reserved": reserve,
                },
            )
            raise BudgetStop("reserved_independent_review") from None
        task = AgentTask(
            id=task_id,
            binding=ctx.binding,
            stage_id=deps.store.run(run_id).stage_id,
            cohort_id=ctx.cohort_id,
            objective=module.SPEC.system_prompt,
            package_ref=ctx.package.id if ctx.package else ctx.binding.source_snapshot_id,
            design_version=ctx.binding.design_version,
            allowed_tools=(
                [t for t in module.TOOLS if t in {"read_package", "read_design", "read_issues"}]
                if module is design and focus.get("mode") == "clarification"
                else list(module.TOOLS)
            ),
            issue_refs=[i.id for i in focus.get("issues", focus.get("batch", []))],
            budget=TaskBudget(
                max_turns=deps.run.task_max_turns,
                max_tokens=min(deps.run.task_max_tokens, available),
            ),
            exit_conditions=["Valid structured submission"],
        )
        if module is explorer:
            if deps.run.max_actions - deps.store.run(run_id).actions_done < 2:
                raise BudgetStop("reserved_generation_and_selection")
            deps.store.save_exploration_task(run_id, task)
    ctx.task_id = task_id
    tools = build_tools(ctx)
    submit = module.submit_tool(ctx, **focus)
    result = run_agent(
        module.SPEC,
        task,
        module.brief(ctx, **focus),
        tools,
        submit,
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
        raise BudgetStop(result.stop_reason)
    return result


def save_decisions(store: Store, run_id: str, payload: dict) -> None:
    for data in payload.get("decisions", []):
        decision = ResearcherDecision.model_validate(data)
        if decision.run_id != run_id or decision.responses or decision.application:
            raise ValueError("agent cannot author researcher responses")
        decision.binding = store.binding(run_id)
        store.save_decision(decision)
