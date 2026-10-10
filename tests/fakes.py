"""Scripted external services; domain behavior always uses production modules."""

from copy import deepcopy

from experiment_design.runtime.jev import Decision


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
