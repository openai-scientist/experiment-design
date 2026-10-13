"""Coordination needs scientific content to distinguish a draft from no useful work."""

from experiment_design.config import RunConfig
from experiment_design.persistence.store import Store
from experiment_design.workflow import jev_state
from tests.support.scenarios import source_context, source_snapshot, supported_design


def test_jev_receives_committed_intent_and_provisional_draft_without_inventing_readiness(tmp_path):
    store = Store(tmp_path / "scientific-state.db")
    try:
        rid = store.create_staged_run(source_snapshot(), {})
        empty = jev_state(store, rid, RunConfig())
        assert empty["research"] is None and empty["draft"] is None
        context = source_context()
        context.unknowns = ["Estimand not confirmed", "ICC needs verification"]
        store.save_context(rid, context)
        d = supported_design()
        d.claim.estimand.origin = "designer_default"
        store.add_version(rid, d, author="design", parent=None)
        state = jev_state(store, rid, RunConfig())
        assert state["research"]["question"] == "Is AI feedback non-inferior?"
        assert state["research"]["unknowns"] == context.unknowns
        assert state["draft"]["estimand"] == {
            "value": "ITT difference in class means",
            "origin": "designer_default",
        }
        assert state["draft"]["model"] == "Pooled t test of cluster means"
        assert state["calculations"] == []
        assert store.assessment(rid) is None and not store.approvals(rid)
    finally:
        store.close()
