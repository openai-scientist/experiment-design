"""Scripted external provider responses for deterministic workflow checks."""

import json

from experiment_design.runtime.jev import Decision
from experiment_design.runtime.llm import LLMResponse, ToolCall


def totals_of(task: dict, name: str) -> int:
    return next(d["lines"] for d in task["documents"] if d["name"] == name)


def read_documents(messages) -> dict[str, str]:
    """The workspace text an agent actually read, rebuilt from its read tool results."""
    lines: dict[str, dict[int, str]] = {}

    def add(name: str, text: str) -> None:
        for row in text.splitlines():
            number, _, line = row.partition(": ")
            lines.setdefault(name, {})[int(number)] = line

    for name, text in json.loads(messages[1]["content"]).get("required_documents", {}).items():
        add(name, text)
    for message in messages:
        if message["role"] != "tool":
            continue
        result = json.loads(message["content"])
        if "total_lines" in result:
            add(result["name"], result["text"])
    return {name: "\n".join(rows[n] for n in sorted(rows)) for name, rows in lines.items()}


class Reader:
    """Reads any document still not covered before the scripted answer; required ones come given."""

    def __init__(self):
        self.inputs = []
        self.starts = []

    def reading_calls(self, messages) -> list[ToolCall]:
        self.inputs.append(messages.copy())
        if len(messages) == 2:
            self.starts.append(len(self.inputs) - 1)
        task = json.loads(messages[1]["content"])
        covered: dict[str, set[int]] = {
            name: set(range(1, totals_of(task, name) + 1)) for name in task["required"]
        }
        for message in messages:
            if message["role"] == "tool":
                result = json.loads(message["content"])
                if "total_lines" in result:
                    covered.setdefault(result["name"], set()).update(
                        range(result["start_line"], result["end_line"] + 1)
                    )
        calls = []
        totals = {d["name"]: d["lines"] for d in task["documents"]}
        for name in task["required"]:
            missing = [n for n in range(1, totals[name] + 1) if n not in covered.get(name, set())]
            if missing:
                args = {"name": name, "start_line": missing[0]}
                calls.append(
                    ToolCall(
                        id=f"read-{len(self.inputs)}-{name}",
                        name="read",
                        arguments=json.dumps(args),
                    )
                )
        return calls

    def documents(self, invocation: int) -> dict[str, str]:
        """Documents read by the Nth specialist invocation (0-based)."""
        end = self.starts[invocation + 1] if invocation + 1 < len(self.starts) else len(self.inputs)
        return read_documents(self.inputs[end - 1])

    def task(self, invocation: int) -> dict:
        return json.loads(self.inputs[self.starts[invocation]][1]["content"])


class Provider(Reader):
    def __init__(self, outputs):
        super().__init__()
        self.outputs = iter(outputs)

    def complete(self, messages, tools):
        calls = self.reading_calls(messages)
        if not calls:
            body = dict(next(self.outputs))
            task = json.loads(messages[1]["content"])
            documents = read_documents(messages)
            if task.get("task", "").startswith("Role: revision.") and "responses" not in body:
                body["responses"] = [
                    {
                        "finding_id": f["finding_id"],
                        "outcome": "not_fixed",
                        "note": "Scripted fixture did not supply a disposition",
                    }
                    for f in json.loads(documents.get("revision_findings", "[]"))
                ]
            calls = [ToolCall(id="submit", name="submit", arguments=json.dumps(body))]
        return LLMResponse(
            content=None, tool_calls=calls, tokens_in=2, tokens_out=3, tokens_cached=1
        )


class Judge:
    def decide(self, state, questions):
        return {
            key: Decision(next(iter(q.options)), {}, 1.0, tokens=1) for key, q in questions.items()
        }


class ToolProvider(Reader):
    """Scripted (tool name, arguments) responses, one tool call per turn, after required reads."""

    def __init__(self, calls):
        super().__init__()
        self.calls = iter(calls)

    def complete(self, messages, tools):
        calls = self.reading_calls(messages)
        if not calls:
            name, args = next(self.calls)
            task = json.loads(messages[1]["content"])
            if (
                name == "submit"
                and task.get("task", "").startswith("Role: revision.")
                and "responses" not in args
            ):
                documents = read_documents(messages)
                args = args | {
                    "responses": [
                        {
                            "finding_id": f["finding_id"],
                            "outcome": "not_fixed",
                            "note": "Scripted fixture did not supply a disposition",
                        }
                        for f in json.loads(documents.get("revision_findings", "[]"))
                    ]
                }
            calls = [ToolCall(id=name, name=name, arguments=json.dumps(args))]
        return LLMResponse(content=None, tool_calls=calls, tokens_in=2, tokens_out=3)


class Scripted:
    """Scripted turns; each turn is a list of (tool name, arguments) issued together."""

    def __init__(self, turns):
        self.turns = iter(turns)
        self.inputs = []
        self.tools = []

    def complete(self, messages, tools):
        self.inputs.append(messages.copy())
        self.tools.append(tools)
        calls = [
            ToolCall(id=f"{len(self.inputs)}-{i}-{name}", name=name, arguments=json.dumps(args))
            for i, (name, args) in enumerate(next(self.turns))
        ]
        return LLMResponse(content=None, tool_calls=calls, tokens_in=2, tokens_out=3)
