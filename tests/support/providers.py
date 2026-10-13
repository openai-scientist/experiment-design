"""Scripted external services; domain behavior always uses production modules."""

import json
from copy import deepcopy
from uuid import uuid4

from experiment_design.runtime.jev import Decision, Question
from experiment_design.runtime.llm import LLMResponse, ToolCall


class ScriptedJudge:
    def __init__(self, script):
        self.script = iter(script)
        self.calls = []

    def decide(self, state, questions):
        self.calls.append((deepcopy(state), deepcopy(questions)))
        choices = next(self.script)
        assert set(choices) == set(questions), (choices, questions)
        for name, choice in choices.items():
            assert choice in questions[name].options, (choice, questions[name].options)
        return {
            name: Decision(
                choice, {option: float(option == choice) for option in questions[name].options}, 1.0
            )
            for name, choice in choices.items()
        }


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def complete(self, messages, tools):
        self.calls.append((deepcopy(messages), deepcopy(tools)))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response(messages, tools) if callable(response) else response


def tool_call(name, args, call_id=None):
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id=call_id or uuid4().hex[:8],
                name=name,
                arguments=args if isinstance(args, str) else json.dumps(args),
            )
        ],
        tokens_in=10,
        tokens_out=5,
    )


def submit(name, payload):
    return tool_call(name, payload)


def questions():
    return {"next": Question("Choose next action", {"REVIEW": "Review", "STOP": "Stop"})}
