"""One bounded observe/tool/submit loop for every specialist agent."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import uuid4

from experiment_design.contracts import AgentResult, AgentTask, Observation
from experiment_design.persistence.store import Store, TaskRecord
from experiment_design.runtime.llm import LLMClient


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


def tool_schemas(offered: dict[str, Tool]) -> list[dict]:
    return [
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
    task_id = task.id or "t" + uuid4().hex[:8]
    durable = task.binding is not None
    record = None
    if durable:
        if not task.id:
            raise ValueError("bound tasks require a stable task id")
        try:
            record = store.task(task_id)
        except KeyError:
            record = TaskRecord(task=task, run_id=run_id)
        if record.run_id != run_id or record.task != task:
            raise ValueError("task identity or binding changed")
        if record.submission is not None:
            return record.submission
        if record.status != "running":
            record.attempt += 1
        record.status = "running"
        store.checkpoint_task(record)
        call_ids.update(o.id for o in store.accepted_observations(task_id))
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
    schemas = tool_schemas(offered)
    messages = [
        {"role": "system", "content": spec.system_prompt},
        {"role": "user", "content": brief},
    ]
    result = AgentResult(findings_or_changes=None, completion_status="failed")
    if durable:
        schema_hash = sha256(json.dumps(schemas, sort_keys=True).encode()).hexdigest()
        if record.schema_hash and record.schema_hash != schema_hash:
            raise ValueError("task tool schemas changed on resume")
        record.schema_hash = schema_hash
        messages = record.messages or messages
        if record.usage:
            result = AgentResult.model_validate(record.usage)
        store.checkpoint_task(record)
    try:
        for _ in range(result.llm_calls, task.budget.max_turns):
            if result.tokens_in + result.tokens_out >= task.budget.max_tokens:
                result.stop_reason = "token_limit"
                break
            result.llm_calls += 1
            if durable:
                store.append_event(run_id, "provider.attempt", {"provider": "llm"}, **meta)
            response = llm.complete(messages, schemas)
            if durable:
                store.append_event(
                    run_id,
                    "provider.usage",
                    {
                        "tokens_in": response.tokens_in,
                        "tokens_out": response.tokens_out,
                        "tokens_cached": response.tokens_cached or 0,
                        "cache_usage_missing_calls": int(response.tokens_cached is None),
                        "llm_calls": 1,
                    },
                    **meta,
                )
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
                observation_id = task_id + ":" + call.id if durable else call.id
                try:
                    if call.name not in offered:
                        raise ValueError(f"tool {call.name!r} is unknown or not allowed")
                    args = json.loads(call.arguments)
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must be a JSON object")
                    observation = offered[call.name].handler(args)
                    content = json.dumps(observation, ensure_ascii=False, allow_nan=False)
                    if call.name != submit.name:
                        if durable:
                            store.save_observation(
                                run_id,
                                Observation(
                                    id=observation_id,
                                    task_id=task_id,
                                    binding=task.binding,
                                    tool_name=call.name,
                                    arguments=args,
                                    result=observation
                                    if isinstance(observation, dict)
                                    else {"value": observation},
                                    accepted=True,
                                    evidence_ref=f"tool:{observation_id}",
                                ),
                            )
                        call_ids.add(observation_id)
                    ok = True
                except SubmitRejected as exc:
                    observation = {"error": exc.errors}
                    content = json.dumps(observation, ensure_ascii=False)
                except Exception as exc:
                    observation = {"error": str(exc)}
                    content = json.dumps(observation, ensure_ascii=False)
                result.tool_observations.append(observation_id)
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
                    feedback = {"result": observation, "evidence_ref": f"tool:{observation_id}"}
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
                    if durable:
                        record.messages = messages
                        record.usage = result.model_dump()
                        store.checkpoint_task(record)
                        store.record_submission(task_id, result)
                    return result
                if durable:
                    record.messages = messages
                    record.usage = result.model_dump()
                    store.checkpoint_task(record)
        result.completion_status = "budget_exhausted"
        result.stop_reason = result.stop_reason or "turn_limit"
        return result
    except BaseException:
        if durable:
            record.status = "interrupted"
            record.messages = messages
            record.usage = result.model_dump()
            store.checkpoint_task(record)
            store.append_event(
                run_id, "budget.stop", {"scope": "task", "reason": "provider_failure"}, **meta
            )
        raise
    finally:
        if durable and result.completion_status == "budget_exhausted":
            record.status = "stopped"
            record.messages = messages
            record.usage = result.model_dump()
            store.checkpoint_task(record)
            store.append_event(
                run_id, "budget.stop", {"scope": "task", "reason": result.stop_reason}, **meta
            )
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
                "usage_accounted": durable,
            },
            **meta,
        )
