import pytest
from conftest import design, package

from experiment_design.contracts import ReviewIssue
from experiment_design.runtime.store import Store


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / "exd.db")
    run = store.create_run(package(), {"provider": "configured"})
    yield store, run
    store.close()


def issue(run):
    return ReviewIssue(
        run_id=run,
        raised_by="skeptic",
        version_raised=1,
        severity="MAJOR",
        paths=["/analysis/model"],
        mechanism="Omitted confounder",
        consequence="Biased inference",
        basis="reasoning",
    )


def test_version_numbers_increase_and_latest(state):
    store, run = state
    first = store.add_version(run, design(), author="design", parent=None)
    event = store.events(run)[-1]
    assert (event.type, event.design_version, event.data["design_hash"]) == (
        "design.version",
        first.version,
        first.design_hash,
    )
    d = design()
    d.analysis.alpha = 0.05
    second = store.add_version(run, d, author="design", parent=1)
    assert (first.version, second.version) == (1, 2)
    assert store.version(run).design.analysis.alpha == 0.05
    assert store.version(run, 1) == first


def test_issue_id_assigned_once(state):
    store, run = state
    first = store.save_issue(issue(run))
    first.status = "resolved"
    updated = store.save_issue(first)
    second = store.save_issue(issue(run))
    assert (first.id, updated.id, second.id) == ("I1", "I1", "I2")
    assert len(store.issues(run)) == 2


def test_unknown_event_type_rejected(state):
    store, run = state
    with pytest.raises(ValueError, match="unknown event"):
        store.append_event(run, "fabricated", {})
    assert len(store.events(run)) == 1


def test_events_after_id(state):
    store, run = state
    cursor = store.events(run)[-1].event_id
    store.update_run(run, status="REVIEWABLE")
    events = store.events(run, after_id=cursor)
    assert [(e.type, e.data["status"]) for e in events] == [("run.status", "REVIEWABLE")]


def test_reopen_store_persists(tmp_path):
    path = tmp_path / "exd.db"
    first = Store(path)
    run = first.create_run(package(), {"provider": "configured"})
    first.add_version(run, design(), author="design", parent=None)
    first.save_issue(issue(run))
    first.close()
    second = Store(path)
    try:
        assert second.run(run).package == package()
        assert second.run(run).status == "RUNNING"
        assert second.run(run).config == {"provider": "configured"}
        assert second.version(run).version == 1
        assert [i.id for i in second.issues(run)] == ["I1"]
    finally:
        second.close()


def test_usage_sums_tasks_and_jev(state):
    store, run = state
    store.append_event(
        run,
        "task.finished",
        {
            "tokens_in": 10,
            "tokens_out": 4,
            "llm_calls": 2,
            "tokens_cached": 7,
            "cache_usage_missing_calls": 1,
        },
    )
    store.append_event(run, "task.finished", {"tokens_in": 3, "tokens_out": 5, "llm_calls": 1})
    store.append_event(run, "decision", {"by": "jev", "choice": "REVIEW"})
    store.append_event(run, "decision", {"by": "only_option", "choice": "STOP"})
    store.update_run(run, actions_done=2)
    assert store.usage(run).model_dump() == {
        "llm_tokens": 22,
        "llm_calls": 3,
        "jev_calls": 1,
        "actions": 2,
        "llm_cached_tokens_reported": 7,
        "llm_cache_usage_missing_calls": 2,
        "llm_usage_missing_calls": 0,
        "jev_tokens_reported": 0,
        "jev_usage_missing_calls": 0,
    }


def test_transaction_rolls_back_all(state):
    store, run = state
    before = store.events(run)
    with pytest.raises(RuntimeError), store.transaction():
        store.add_version(run, design(), author="design", parent=None)
        store.save_issue(issue(run))
        raise RuntimeError("process failed")
    assert store.version(run) is None
    assert store.issues(run) == []
    assert store.events(run) == before
