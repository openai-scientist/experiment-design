"""Existing state and choice builders shared by presentation behavior tests."""

from experiment_design.contracts import DecisionOption, ResearcherDecision
from experiment_design.persistence.store import Store
from tests.support.scenarios import source_context, source_snapshot, supported_design


def study(tmp_path):
    store = Store(tmp_path / "presentation.db")
    rid = store.create_staged_run(source_snapshot(), {})
    store.save_context(rid, source_context())
    store.add_version(rid, supported_design(), author="design", parent=None)
    return store, rid


def choice(store, rid):
    return ResearcherDecision(
        id="estimand-choice",
        run_id=rid,
        revision=3,
        binding=store.binding(rid),
        status="AWAITING_RESEARCHER",
        problem="Confirm the target effect",
        affected_paths=["/claim/estimand", "/analysis/model"],
        dependencies=["Population and assignment interpretation"],
        recommendation="Consider assigned treatment; confirmation still needed",
        options=[
            DecisionOption(
                id="itt",
                label="Assigned treatment effect",
                proposal={"claim.estimand": "Effect in randomized students"},
                consequences=["Retains randomized comparison"],
                uncertainty="Adherence does not define the target population",
                evidence_refs=["S1"],
            )
        ],
    )
