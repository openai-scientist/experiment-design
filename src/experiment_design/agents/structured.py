"""Bounded schema submissions with the same conservative usage policy as specialists."""

import hashlib
import json

from pydantic import ValidationError

from experiment_design.agents.prompts import prompt
from experiment_design.agents.tools import _known, _tool, _usage_ceiling
from experiment_design.config import RunConfig
from experiment_design.contracts import Record, WorkflowError
from experiment_design.persistence.store import RunStore
from experiment_design.runtime.llm import LLMClient


def structured_call[T: Record](
    store: RunStore, llm: LLMClient, cfg: RunConfig, role: str, payload: dict, model: type[T]
) -> T:
    messages = [
        {"role": "system", "content": prompt(role)},
        {
            "role": "user",
            "content": json.dumps({"required": [], "documents": [], "input": payload}),
        },
    ]
    tools = [_tool("submit", "Submit the structured result", model.model_json_schema())]
    scope = f"structured:{role}:{store.state.attempt}:{store.state.stage}"
    invocation = sum(
        event["kind"] == "structured.accepted" and event["data"].get("scope") == scope
        for event in store.events()
    )
    task = f"{scope}:{invocation}"
    prior = [
        event["data"]
        for event in store.events()
        if event["kind"] == "provider.response" and event["data"].get("task") == task
    ]
    task_tokens = sum(attempt["tokens"] or 0 for attempt in prior)
    attempts_used = len(prior)
    for turn in range(2):
        if store.state.usage_unknown:
            raise WorkflowError("Provider usage is unknown")
        if (
            task_tokens >= cfg.task_max_tokens
            or store.state.llm_tokens >= store.state.llm_token_limit
        ):
            raise WorkflowError("LLM token budget exhausted")
        if attempts_used >= cfg.task_max_turns:
            raise WorkflowError("LLM turn budget exhausted")
        if hasattr(llm, "max_attempts"):
            llm.max_attempts = min(llm.cfg.max_retries + 1, cfg.task_max_turns - attempts_used)
        store.event(
            "provider.attempt",
            {
                "task": task,
                "turn": turn,
                "messages_sha256": hashlib.sha256(
                    json.dumps(messages, sort_keys=True).encode()
                ).hexdigest(),
                "message_count": len(messages),
            },
        )
        response, failure = None, None
        try:
            response = llm.complete(messages, tools)
        except Exception as exc:
            failure = exc
        attempts = getattr(llm, "last_attempts", None) or [
            {
                "status": "error" if failure else "success",
                "tokens_in": response.tokens_in if response else None,
                "tokens_out": response.tokens_out if response else None,
                "tokens_cached": response.tokens_cached if response else None,
            }
        ]
        ceiling = _usage_ceiling(llm, attempts) if response and not failure else None
        unknown = False
        for attempt in attempts:
            known = _known(attempt.get("tokens_in")) and _known(attempt.get("tokens_out"))
            usage = attempt["tokens_in"] + attempt["tokens_out"] if known else ceiling
            unknown |= usage is None
            task_tokens += usage or 0
            store.event(
                "provider.response",
                {
                    "task": task,
                    "turn": turn,
                    **attempt,
                    "tokens": usage,
                    **({"usage_bound": "ceiling"} if not known and usage is not None else {}),
                },
            )
            store.update(llm_tokens=store.state.llm_tokens + (usage or 0))
        attempts_used += len(attempts)
        if unknown:
            store.update(status="USAGE_UNKNOWN", usage_unknown=True)
            raise WorkflowError("Provider usage is unknown") from failure
        if failure:
            raise WorkflowError("Provider call failed") from failure
        if (
            task_tokens > cfg.task_max_tokens
            or store.state.llm_tokens > store.state.llm_token_limit
            or attempts_used > cfg.task_max_turns
        ):
            raise WorkflowError("LLM token or turn budget exceeded")
        try:
            if len(response.tool_calls) != 1 or response.tool_calls[0].name != "submit":
                raise ValueError("exactly one submit tool call is required")
            result = model.model_validate_json(response.tool_calls[0].arguments)
        except (ValueError, ValidationError) as exc:
            messages.append(
                {"role": "user", "content": f"Invalid submission: {exc}. Submit again."}
            )
            continue
        store.event("structured.accepted", {"scope": scope, "task": task, "role": role})
        return result
    raise WorkflowError("Invalid structured submission after one retry")
