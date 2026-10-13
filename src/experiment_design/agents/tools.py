"""Bounded workspace agents: read, search, source, calculate, check, then submit."""

import hashlib
import json
import re

from pydantic import ValidationError

from experiment_design.agents.prompts import prompt
from experiment_design.config import RunConfig
from experiment_design.contracts import AgentOutput, CalculationRequest, WorkflowError
from experiment_design.persistence.store import RunStore
from experiment_design.runtime.llm import LLMClient
from experiment_design.science.evidence import supports_number
from experiment_design.science.power import calculate

_READ_LINES = 400
_READ_CAP = 1000


def _tool(name: str, description: str, parameters: dict) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def _exact_span(sources: dict[str, str], name: str | None, span: str | None) -> bool:
    """A usable span is exact supplied text with enough content to identify its place."""
    return bool(
        name in sources and span and span in sources[name] and len(re.sub(r"\s", "", span)) >= 12
    )


def _index(workspace: dict[str, str]) -> list[dict]:
    """Name, line count and first non-blank line of each document; never the content."""
    return [
        {
            "name": name,
            "lines": len(text.splitlines()),
            "first_line": next((line.strip() for line in text.splitlines() if line.strip()), "")[
                :120
            ],
        }
        for name, text in workspace.items()
    ]


def _ranges(numbers: list[int]) -> str:
    spans: list[list[int]] = []
    for number in numbers:
        if spans and number == spans[-1][1] + 1:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return ",".join(f"{a}-{b}" if a != b else str(a) for a, b in spans)


def _unread(workspace: dict[str, str], required: list[str], covered: dict[str, set[int]]) -> str:
    parts = []
    for name in required:
        total = len(workspace[name].splitlines())
        missing = [n for n in range(1, total + 1) if n not in covered.get(name, set())]
        if missing:
            parts.append(f"{name} (lines {_ranges(missing)})")
    return ", ".join(parts)


def _int_arg(args: dict, key: str, default: int) -> int:
    value = args.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _digest(read_result: dict) -> dict:
    """Journal a read by identity; the model alone receives the text."""
    text = read_result["text"]
    return {k: v for k, v in read_result.items() if k != "text"} | {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()
    }


def _list_arg(args: dict, key: str) -> list:
    value = args[key]
    if not isinstance(value, list) or not value:
        raise ValueError(f"{key} must be a non-empty list")
    return value


def _quote(store: RunStore, workspace: dict[str, str], args: object, accepted: set[str]) -> dict:
    """Supplied sources are evidence; other workspace documents are quoted locations
    (plan text, actual replies) and never count as source values."""
    if not isinstance(args, dict):
        raise ValueError("Each quote must be an object with name and span")
    name, span = args.get("name"), args.get("span")
    if _exact_span(store.sources(), name, span):
        kind = "source"
    elif _exact_span(workspace, name, span):
        kind = "document"
    else:
        raise ValueError(
            f"Span is not an exact quote of at least 12 non-whitespace characters from {name!r}"
        )
    ref = f"{kind}:" + hashlib.sha256((name + span).encode()).hexdigest()
    accepted.add(ref)
    return {"evidence_ref": ref, "source": name, "kind": kind, "span": span}


def _quote_or_error(
    store: RunStore, workspace: dict[str, str], args: object, accepted: set[str]
) -> dict:
    try:
        return _quote(store, workspace, args, accepted)
    except ValueError as exc:
        return {"accepted": False, "error": str(exc)}


def _read(workspace: dict[str, str], covered: dict[str, set[int]], args: dict) -> dict:
    name = args.get("name")
    if name not in workspace:
        raise ValueError(f"Unknown document {name!r}")
    lines = workspace[name].splitlines()
    start = _int_arg(args, "start_line", 1)
    limit = _int_arg(args, "max_lines", _READ_LINES)
    if limit < 1:
        raise ValueError("max_lines must be at least 1")
    if start < 1 or start > max(len(lines), 1):
        raise ValueError(f"start_line out of range; {name} has {len(lines)} lines")
    chosen = range(start, min(start + min(limit, _READ_CAP), len(lines) + 1))
    covered.setdefault(name, set()).update(chosen)
    return {
        "name": name,
        "start_line": start,
        "end_line": chosen[-1] if chosen else start - 1,
        "total_lines": len(lines),
        "text": "\n".join(f"{n}: {lines[n - 1]}" for n in chosen),
    }


def _search(workspace: dict[str, str], args: dict) -> dict:
    terms = str(args.get("query", "")).lower().split()
    if not terms:
        raise ValueError("query must contain at least one term")
    limit = _int_arg(args, "max_hits", 20)
    if limit < 1:
        raise ValueError("max_hits must be at least 1")
    hits = []
    truncated = False
    for name, text in workspace.items():
        for number, line in enumerate(text.splitlines(), 1):
            if all(term in line.lower() for term in terms):
                if len(hits) == limit:
                    truncated = True
                    break
                hits.append({"name": name, "line": number, "text": line[:300]})
        if truncated:
            break
    return {"hits": hits, "truncated": truncated}


def _problems(
    args: object,
    workspace: dict[str, str],
    required: list[str],
    covered: dict[str, set[int]],
    accepted_refs: set[str],
) -> tuple[AgentOutput | None, list[str]]:
    """Every reason a submission would be rejected; shared by check and submit."""
    try:
        output = AgentOutput.model_validate(args)
    except ValidationError as exc:
        return None, [
            f"{'.'.join(map(str, e['loc'])) or 'output'}: {e['msg']}"
            for e in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
    problems = []
    unknown = sorted(
        {ref for f in output.findings for ref in f.evidence_refs if ref not in accepted_refs}
    )
    if unknown:
        problems.append(
            "Finding references evidence not accepted in this task: "
            + ", ".join(unknown[:5])
            + ". Use only evidence_ref values returned by source or calculate, "
            "or leave evidence_refs empty"
        )
    if output.needs_input and not output.question.strip():
        problems.append("needs_input requires a concrete question")
    unread = _unread(workspace, required, covered)
    if unread:
        problems.append(f"Required documents not fully read: {unread}")
    return output, problems


def run_specialist(
    role: str,
    store: RunStore,
    llm: LLMClient,
    cfg: RunConfig,
    workspace: dict[str, str],
    required: list[str],
) -> AgentOutput:
    missing = [name for name in required if name not in workspace]
    if missing:
        raise WorkflowError(f"Required documents missing from workspace: {', '.join(missing)}")
    task_text = (
        f"Role: {role}. Inspect the document index, read every required document fully, "
        "then check and submit."
    )
    messages = [
        {"role": "system", "content": prompt(role)},
        {
            "role": "user",
            "content": json.dumps(
                {"task": task_text, "required": required, "documents": _index(workspace)},
                ensure_ascii=False,
            ),
        },
    ]
    covered: dict[str, set[int]] = {}
    tools = [
        _tool("submit", "Submit the final scientific output", AgentOutput.model_json_schema()),
        _tool(
            "check",
            "Advisory: report every problem submit would reject, without submitting",
            AgentOutput.model_json_schema(),
        ),
        _tool(
            "read",
            "Read numbered lines. Pass names to read several whole documents in one call "
            "(preferred: turns are limited), or name with start_line/max_lines for a range",
            {
                "type": "object",
                "properties": {
                    "names": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "name": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "max_lines": {"type": "integer", "minimum": 1},
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
        _tool(
            "search",
            "Case-insensitive search for all terms on one line across workspace documents",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_hits": {"type": "integer", "minimum": 1},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        _tool(
            "calculate",
            "Optional supported computation; explicit calculator_id required",
            CalculationRequest.model_json_schema(),
        ),
        _tool(
            "source",
            "Quote exact spans from workspace documents to cite them in findings: verbatim "
            "quotes (at least 12 non-whitespace characters), not line numbers. Pass quotes to "
            "obtain several refs in one call (preferred: turns are limited). Supplied sources "
            "give kind 'source' (evidence); other documents give kind 'document' (a quoted "
            "location, not evidence of a value)",
            {
                "type": "object",
                "properties": {
                    "quotes": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "span": {"type": "string"}},
                            "required": ["name", "span"],
                        },
                    },
                    "name": {"type": "string"},
                    "span": {
                        "type": "string",
                        "description": "Exact verbatim quote copied from the document, at least "
                        "12 non-whitespace characters; never line numbers or a paraphrase",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        ),
    ]
    if store.state.usage_unknown:
        raise WorkflowError("Provider usage is unknown")
    accepted_refs: set[str] = set()
    scope = f"{role}:{store.state.attempt}:{store.state.stage}"
    # Each invocation has its own allowance; an interrupted one keeps its number until accepted.
    accepted = sum(
        1
        for e in store.events()
        if e["kind"] == "agent.accepted" and e["data"]["task"].rsplit(":", 1)[0] == scope
    )
    task = f"{scope}:{accepted}"
    prior = [
        e["data"]
        for e in store.events()
        if e["kind"] == "provider.response" and e["data"].get("task") == task
    ]
    task_tokens = sum(e["tokens"] or 0 for e in prior)
    attempts_used = len(prior)
    for turn in range(len(prior), cfg.task_max_turns):
        if (
            task_tokens >= cfg.task_max_tokens
            or store.state.llm_tokens >= store.state.llm_token_limit
        ):
            raise WorkflowError("LLM token budget exhausted")
        if attempts_used >= cfg.task_max_turns:
            break
        if hasattr(llm, "max_attempts"):
            configured = (
                getattr(getattr(llm, "cfg", None), "max_retries", cfg.task_max_turns - 1) + 1
            )
            llm.max_attempts = min(configured, cfg.task_max_turns - attempts_used)
        canonical = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        store.event(
            "provider.attempt",
            {
                "task": task,
                "turn": turn,
                "messages_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "message_count": len(messages),
            },
        )
        response = None
        failure = None
        try:
            response = llm.complete(messages, tools)
        except Exception as exc:
            failure = exc
        attempts = getattr(llm, "last_attempts", None)
        if not attempts:
            attempts = [
                {
                    "status": "error" if failure else "success",
                    "tokens_in": response.tokens_in if response else None,
                    "tokens_out": response.tokens_out if response else None,
                    "tokens_cached": response.tokens_cached if response else None,
                    "response": response.model_dump() if response else None,
                }
            ]
        attempts_used += len(attempts)
        unknown = False
        for attempt in attempts:
            tokens_in, tokens_out = attempt.get("tokens_in"), attempt.get("tokens_out")
            known = (
                isinstance(tokens_in, int)
                and isinstance(tokens_out, int)
                and tokens_in >= 0
                and tokens_out >= 0
            )
            usage = tokens_in + tokens_out if known else 0
            unknown |= not known
            task_tokens += usage
            store.event(
                "provider.response",
                {"task": task, "turn": turn, **attempt, "tokens": usage if known else None},
            )
            store.update(llm_tokens=store.state.llm_tokens + usage)
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
        assistant: dict = {"role": "assistant", "content": response.content}
        if response.tool_calls:
            assistant["tool_calls"] = [
                {
                    "id": t.id,
                    "type": "function",
                    "function": {"name": t.name, "arguments": t.arguments},
                }
                for t in response.tool_calls
            ]
        messages.append(assistant)
        if not response.tool_calls:
            messages.append(
                {
                    "role": "user",
                    "content": "Plain-text replies are discarded. Put your document in submit "
                    "(markdown and the other fields), or continue with read, search, source, "
                    "calculate or check.",
                }
            )
        submission = None
        for call in response.tool_calls:
            try:
                args = json.loads(call.arguments)
                if not isinstance(args, dict):
                    raise ValueError("Tool arguments must be a JSON object")
                if call.name in {"submit", "check"}:
                    output, problems = _problems(args, workspace, required, covered, accepted_refs)
                    if call.name == "check":
                        result = {"ok": not problems, "problems": problems}
                    else:
                        if submission is not None:
                            problems.append("Only one submit per response is allowed")
                        if problems:
                            result = {
                                "accepted": False,
                                "error": "; ".join(problems),
                                "problems": problems,
                            }
                        else:
                            submission = output
                            result = {"accepted": True}
                elif call.name == "read":
                    if "names" in args:
                        result = {
                            "documents": [
                                _read(workspace, covered, {"name": name, "max_lines": _READ_CAP})
                                for name in _list_arg(args, "names")
                            ]
                        }
                    else:
                        result = _read(workspace, covered, args)
                elif call.name == "search":
                    result = _search(workspace, args)
                elif call.name == "source":
                    if "quotes" in args:
                        result = {
                            "quotes": [
                                _quote_or_error(store, workspace, q, accepted_refs)
                                for q in _list_arg(args, "quotes")
                            ]
                        }
                    else:
                        result = _quote(store, workspace, args, accepted_refs)
                elif call.name == "calculate":
                    request = CalculationRequest.model_validate(args)
                    if not request.calculator_id:
                        raise ValueError("Explicit calculator_id required")
                    quantities = [request.margin, request.effect, request.sd]
                    if request.cluster:
                        quantities += [request.cluster.size, request.cluster.icc]
                    sources = store.sources()
                    for quantity in quantities:
                        if (
                            quantity
                            and quantity.provenance == "source"
                            and not (
                                _exact_span(sources, quantity.source_ref, quantity.source_span)
                                and supports_number(quantity.value, quantity.source_span)
                            )
                        ):
                            raise ValueError("Calculation value/span lacks supplied source support")
                    computed = calculate(request)
                    ref = (
                        "calculation:"
                        + hashlib.sha256(
                            json.dumps(request.model_dump(), sort_keys=True).encode()
                        ).hexdigest()
                    )
                    result = computed.model_dump(mode="json")
                    if result.get("status") == "computed":
                        accepted_refs.add(ref)
                    result["evidence_ref"] = ref
                    store.event(
                        "calculation",
                        {
                            "task": task,
                            "request": request.model_dump(mode="json"),
                            "result": result,
                        },
                    )
                else:
                    raise ValueError("Unknown tool")
            except (ValueError, TypeError, KeyError, ValidationError) as exc:
                result = {"accepted": False, "error": str(exc)}
            logged = result
            if call.name == "read" and "text" in result:
                logged = _digest(result)
            elif call.name == "read" and "documents" in result:
                logged = {"documents": [_digest(d) for d in result["documents"]]}
            store.event(
                "tool.result",
                {"task": task, "tool": call.name, "call_id": call.id, "result": logged},
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        if submission is not None:
            store.event("agent.accepted", {"task": task})
            return submission
    raise WorkflowError("Specialist turn allowance exhausted without accepted submission")
