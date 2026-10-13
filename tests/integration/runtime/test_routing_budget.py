import pytest

from experiment_design.runtime.jev import JudgeError
from tests.support.providers import questions
from tests.support.scenarios import seeded_run


def test_jev_call_limit_and_missing_usage(tmp_path):
    from experiment_design.config import RunConfig
    from experiment_design.persistence.store import Store
    from experiment_design.runtime.jev import bounded_decide
    from tests.support.builders import package
    from tests.support.providers import ScriptedJudge

    store = Store(tmp_path / "routing.db")
    run = seeded_run(store, package(), {})
    judge = ScriptedJudge([{"next": "REVIEW"}])
    cfg = RunConfig(jev_max_calls=1)
    bounded_decide(judge, store, run, cfg, {}, questions())
    assert store.usage(run).jev_usage_missing_calls == 1
    with pytest.raises(JudgeError, match="call_limit"):
        bounded_decide(judge, store, run, cfg, {}, questions())
    assert len(judge.calls) == 1
    assert store.events(run)[-1].data == {"scope": "run", "reason": "jev_call_limit"}
    store.close()
