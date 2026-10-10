"""One bounded observe/tool/submit loop for every specialist agent."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from experiment_design.contracts import AgentResult, AgentTask
from experiment_design.runtime.llm import LLMClient
from experiment_design.runtime.store import Store


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict], Any]


class SubmitRejected(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    system_prompt: str


def run_agent(
    spec: AgentSpec,
    task: AgentTask,
    brief: str,
    tools: dict[str, Tool],
    submit: Tool,
    llm: LLMClient,
    store: Store,
    run_id: str,
    call_ids: set[str],
) -> AgentResult:
    task_id = "t" + uuid4().hex[:8]
    meta = {
        "agent_id": spec.agent_id,
        "task_id": task_id,
        "design_version": task.design_version,
    }
    store.append_event(
        run_id,
        "task.started",
        {
            "objective": task.objective,
            "allowed_tools": task.allowed_tools,
            "design_version": task.design_version,
            "budget": task.budget.model_dump(),
        },
        **meta,
    )
    offered = {name: tools[name] for name in task.allowed_tools if name in tools}
    offered[submit.name] = submit
    schemas = [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in offered.values()
    ]
    messages = [
        {"role": "system", "content": spec.system_prompt},
        {"role": "user", "content": brief},
    ]
    result = AgentResult(findings_or_changes=None, completion_status="failed")
    try:
        for _ in range(task.budget.max_turns):
            if result.tokens_in + result.tokens_out >= task.budget.max_tokens:
                result.stop_reason = "token_limit"
                break
            result.llm_calls += 1
            response = llm.complete(messages, schemas)
            result.tokens_in += response.tokens_in
            result.tokens_out += response.tokens_out
            if response.tokens_cached is None:
                result.cache_usage_missing_calls += 1
            else:
                result.tokens_cached += response.tokens_cached
            if result.tokens_in + result.tokens_out >= task.budget.max_tokens:
                result.stop_reason = "token_limit"
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                        for call in response.tool_calls
                    ],
                }
            )
            if not response.tool_calls:
                messages.append(
                    {
                        "role": "user",
                        "content": f"Use a tool, or call {submit.name} to finish.",
                    }
                )
            for call in response.tool_calls:
                ok = False
                try:
                    if call.name not in offered:
                        raise ValueError(f"tool {call.name!r} is unknown or not allowed")
                    args = json.loads(call.arguments)
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must be a JSON object")
                    observation = offered[call.name].handler(args)
                    content = json.dumps(observation, ensure_ascii=False, allow_nan=False)
                    ok = True
                    if call.name != submit.name:
                        call_ids.add(call.id)
                except SubmitRejected as exc:
                    observation = {"error": exc.errors}
                    content = json.dumps(observation, ensure_ascii=False)
                except Exception as exc:
                    observation = {"error": str(exc)}
                    content = json.dumps(observation, ensure_ascii=False)
                result.tool_observations.append(call.id)
                store.append_event(
                    run_id,
                    "tool.call",
                    {
                        "call_id": call.id,
                        "tool": call.name,
                        "ok": ok,
                        "summary": content[:300],
                    },
                    **meta,
                )
                feedback = observation
                if ok and call.name != submit.name:
                    feedback = {"result": observation, "evidence_ref": f"tool:{call.id}"}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(
                            feedback, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                        ),
                    }
                )
                if ok and call.name == submit.name:
                    result.findings_or_changes = observation
                    result.rationale = observation.get("rationale", "")
                    result.evidence_refs = observation.get("evidence_refs", [])
                    result.uncertainty = observation.get("uncertainty", "")
                    result.completion_status = "completed"
                    return result
        result.completion_status = "budget_exhausted"
        result.stop_reason = result.stop_reason or "turn_limit"
        return result
    finally:
        store.append_event(
            run_id,
            "task.finished",
            {
                "completion_status": result.completion_status,
                "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out,
                "llm_calls": result.llm_calls,
                "tokens_cached": result.tokens_cached,
                "cache_usage_missing_calls": result.cache_usage_missing_calls,
                "stop_reason": result.stop_reason,
                "budget": task.budget.model_dump(),
            },
            **meta,
        )
