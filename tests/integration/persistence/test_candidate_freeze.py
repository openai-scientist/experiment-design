import pytest

from experiment_design.contracts import AgentResult
from experiment_design.design.exploration import ExplorationSelection
from experiment_design.persistence.store import Store
from tests.support.exploration import exploration_payload, exploration_task, framed_run


def submitted(store, rid):
    task = store.save_exploration_task(rid, exploration_task(store, rid))
    store.record_submission(
        task.id,
        AgentResult(findings_or_changes=exploration_payload(), completion_status="completed"),
    )
    return task


def test_candidate_only_freeze_is_atomic_and_immutable(tmp_path):
    with_store = Store(tmp_path / "exd.db")
    rid = framed_run(with_store)
    task = submitted(with_store, rid)
    choice = ExplorationSelection(task_id=task.id, binding=task.binding, choice="recommend:probe-a")
    with_store.save_exploration_selection(rid, choice)
    saved = with_store.freeze_exploration(rid, task.id)
    history = with_store.events(rid)
    assert with_store.freeze_exploration(rid, task.id) == saved
    with_store.save_exploration_selection(rid, choice)
    assert with_store.events(rid) == history
    assert with_store.version(rid) is None
    assert with_store.approvals(rid) == []
    assert saved.binding.design_version is None
    assert with_store.run(rid).stage_id == "S2"
    assert with_store.run(rid).status == "RUNNING"
    assert with_store.run(rid).actions_done == 2
    with_store.close()


def test_freeze_rejects_foreign_task_or_stale_binding(tmp_path):
    store = Store(tmp_path / "exd.db")
    rid = framed_run(store)
    other = framed_run(store)
    task = submitted(store, other)
    with pytest.raises(ValueError, match="task|run"):
        store.freeze_exploration(rid, task.id)
    own = submitted(store, rid)
    store.update_run(rid, context_revision=2)
    with pytest.raises(ValueError, match="binding"):
        store.save_exploration_selection(
            rid,
            ExplorationSelection(task_id=own.id, binding=own.binding, choice="recommend:probe-a"),
        )
    assert store.exploration_snapshot(rid) is None
    store.close()


def test_freeze_rolls_back_artifact_status_and_journal_on_write_failure(tmp_path):
    store = Store(tmp_path / "exd.db")
    rid = framed_run(store)
    task = submitted(store, rid)
    store.save_exploration_selection(
        rid, ExplorationSelection(task_id=task.id, binding=task.binding, choice="recommend:probe-a")
    )
    before = store.events(rid)
    store.connection.execute("""CREATE TRIGGER fail_update BEFORE INSERT ON artifacts
        WHEN NEW.kind='exploration_snapshot'
        BEGIN SELECT RAISE(FAIL, 'injected failure'); END""")
    with pytest.raises(Exception, match="injected failure"):
        store.freeze_exploration(rid, task.id)
    assert store.events(rid) == before
    assert store.exploration_snapshot(rid) is None
    assert store.run(rid).status == "RUNNING"
    store.connection.execute("DROP TRIGGER fail_update")
    assert store.freeze_exploration(rid, task.id).outcome == "recommendation"
    store.close()


def test_stale_checkpoint_cannot_overwrite_accepted_task(tmp_path):
    store = Store(tmp_path / "exd.db")
    rid = framed_run(store)
    task = store.save_exploration_task(rid, exploration_task(store, rid))
    stale = store.task(task.id)
    store.record_submission(
        task.id,
        AgentResult(findings_or_changes=exploration_payload(), completion_status="completed"),
    )
    before = store.events(rid)
    with pytest.raises(ValueError, match="immutable|accepted"):
        store.checkpoint_task(stale)
    assert store.task(task.id).submission is not None
    assert store.events(rid) == before
    store.close()
