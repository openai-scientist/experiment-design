from types import SimpleNamespace

import pytest

from experiment_design.config import JevConfig
from experiment_design.runtime.jev import JudgeError, Question, TypeSafeJudge


def questions():
    return {"next": Question("Choose", {"REVIEW": "Review", "STOP": "Stop"})}


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


def test_retry_attempts_are_exposed_and_can_be_capped():
    c = Client(error=True)
    judge = TypeSafeJudge(JevConfig(), c)
    judge.max_attempts = 1
    with pytest.raises(JudgeError):
        judge.decide({}, questions())
    assert len(c.calls) == 1
    assert len(judge.last_attempts) == 1
    assert judge.last_attempts[0]["tokens_in"] is None
    assert judge.last_attempts[0]["error_type"] == "RuntimeError"


@pytest.mark.parametrize("bad", [-100, 1.5, True, "12"])
@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
def test_invalid_jev_usage_cannot_reduce_consumed_tokens(bad, field):
    class UsageClient(Client):
        def system_one(self, **kwargs):
            response = super().system_one(**kwargs)
            setattr(response.usage, field, bad)
            return response

    judge = TypeSafeJudge(JevConfig(), UsageClient())
    with pytest.raises(JudgeError, match="usage"):
        judge.decide({}, questions())
    assert judge.last_attempts[-1]["status"] == "error"
    assert judge.last_attempts[-1]["tokens_in"] is None
    assert judge.last_attempts[-1]["tokens_out"] is None


def test_missing_jev_usage_remains_disclosed_under_call_budget():
    class UsageClient(Client):
        def system_one(self, **kwargs):
            response = super().system_one(**kwargs)
            response.usage = None
            return response

    judge = TypeSafeJudge(JevConfig(), UsageClient())
    assert judge.decide({}, questions())["next"].tokens is None
    assert judge.last_attempts[-1]["status"] == "success"
