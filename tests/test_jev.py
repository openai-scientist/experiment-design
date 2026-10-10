from types import SimpleNamespace

import pytest

from experiment_design.config import JevConfig
from experiment_design.runtime.jev import JudgeError, Question, TypeSafeJudge


class Client:
    def __init__(self, choice="REVIEW", error=False):
        self.calls = []
        self.choice = choice
        self.error = error

    def system_one(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise RuntimeError("provider unavailable")
        return SimpleNamespace(
            choices={
                "next": SimpleNamespace(
                    choice=self.choice,
                    probabilities={"REVIEW": 0.9, "STOP": 0.1},
                    confidence=0.8,
                )
            },
            usage=SimpleNamespace(input_tokens=12, output_tokens=3),
        )


def questions():
    return {"next": Question("Choose next action", {"REVIEW": "Review", "STOP": "Stop"})}


def test_decide_maps_response():
    c = Client()
    result = TypeSafeJudge(JevConfig(), c).decide({"version": 1}, questions())["next"]
    assert result.choice == "REVIEW"
    assert result.probabilities == {"REVIEW": 0.9, "STOP": 0.1}
    assert result.confidence == 0.8 and result.tokens == 15
    assert c.calls[0]["state"] == {"version": 1}
    assert c.calls[0]["questions"]["next"].criteria == {"REVIEW": "Review", "STOP": "Stop"}


def test_decide_rejects_choice_outside_options():
    with pytest.raises(JudgeError, match="INVALID"):
        TypeSafeJudge(JevConfig(), Client("INVALID")).decide({}, questions())


def test_decide_retries_then_raises():
    c = Client(error=True)
    with pytest.raises(JudgeError, match="provider unavailable"):
        TypeSafeJudge(JevConfig(), c).decide({}, questions())
    assert len(c.calls) == 3


def test_model_passed_only_when_set():
    for model in (None, "judge-model"):
        c = Client()
        TypeSafeJudge(JevConfig(model=model), c).decide({}, questions())
        assert c.calls[0].get("model") == model
        assert ("model" in c.calls[0]) == (model is not None)


def test_jev_call_limit_and_missing_usage(tmp_path):
    from conftest import package
    from fakes import ScriptedJudge

    from experiment_design.config import RunConfig
    from experiment_design.runtime.jev import bounded_decide
    from experiment_design.runtime.store import Store

    store = Store(tmp_path / "routing.db")
    run = store.create_run(package(), {})
    judge = ScriptedJudge([{"next": "REVIEW"}])
    cfg = RunConfig(jev_max_calls=1)
    bounded_decide(judge, store, run, cfg, {}, questions())
    assert store.usage(run).jev_usage_missing_calls == 1
    with pytest.raises(JudgeError, match="call_limit"):
        bounded_decide(judge, store, run, cfg, {}, questions())
    assert len(judge.calls) == 1
    assert store.events(run)[-1].data == {"scope": "run", "reason": "jev_call_limit"}
    store.close()
