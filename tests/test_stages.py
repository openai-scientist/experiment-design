import pytest
from conftest import design, package

from experiment_design.contracts import (
    ArtifactBinding,
    ResearchContext,
    ResearcherDecision,
    SourceArtifact,
    SourceSnapshot,
    StageResult,
)
from experiment_design.runtime.store import Store


def staged(store):
    snapshot = SourceSnapshot(
        id="source-1",
        artifacts=[
            SourceArtifact(ref="main", path="hypothesis.md", text="AI feedback", sha256="abc")
        ],
    )
    run = store.create_staged_run(snapshot, {})
    binding = ArtifactBinding(
        source_snapshot_id=snapshot.id, event_id=store.events(run)[-1].event_id
    )
    return run, binding


def test_stage_context_survives_reopen(tmp_path):
    path = tmp_path / "research.db"
    store = Store(path)
    run, binding = staged(store)
    context = ResearchContext(
        id="context-1", revision=1, source_snapshot_id="source-1", package=package()
    )
    store.commit_stage(
        run,
        StageResult(
            stage_id="S1",
            status="COMPLETE",
            binding=binding,
            output_refs=["source-1"],
            reason="Source captured",
        ),
        "S2",
    )
    store.save_context(run, context)
    store.close()
    reopened = Store(path)
    try:
        assert reopened.context(run).source_snapshot_id == "source-1"
        assert reopened.source_snapshot(run).artifacts[0].text == "AI feedback"
        assert reopened.run(run).stage_id == "S2"
    finally:
        reopened.close()


@pytest.mark.parametrize(
    ("output_refs", "target", "error"),
    [
        pytest.param([], "S4", "originating", id="missing-origin"),
        pytest.param(["invented decision"], "S4", "originating", id="fabricated-origin"),
        pytest.param(["D1"], "S1", "target", id="invalid-target"),
    ],
)
def test_return_requires_originating_issue_or_decision(tmp_path, output_refs, target, error):
    store = Store(tmp_path / "research.db")
    run, binding = staged(store)
    store.update_run(run, stage_id="S5")
    store.save_decision(
        ResearcherDecision(id="D1", run_id=run, binding=binding, problem="Choose sampling")
    )
    before = store.events(run)
    with pytest.raises(ValueError, match=error):
        store.commit_stage(
            run,
            StageResult(
                stage_id="S5",
                status="REVISION_REQUIRED",
                binding=binding,
                output_refs=output_refs,
                reason="Missing uncertainty",
            ),
            target,
        )
    assert store.events(run) == before
    assert store.run(run).stage_id == "S5"
    store.close()


def test_stale_stage_binding_cannot_advance(tmp_path):
    store = Store(tmp_path / "research.db")
    run, binding = staged(store)
    store.add_version(run, design(), author="design", parent=None)
    before = store.events(run)
    with pytest.raises(ValueError, match="binding"):
        store.commit_stage(
            run,
            StageResult(
                stage_id="S1", status="COMPLETE", binding=binding, reason="Source captured"
            ),
            "S2",
        )
    assert store.events(run) == before
    store.close()


def test_historical_snapshot_uses_supporting_record_revisions(tmp_path):
    store = Store(tmp_path / "research.db")
    run, binding = staged(store)
    store.save_context(
        run,
        ResearchContext(
            id="context-1", revision=1, source_snapshot_id="source-1", package=package()
        ),
    )
    version = store.add_version(run, design(), author="design", parent=None)
    binding = binding.model_copy(
        update={"context_revision": 1, "design_version": 1, "design_hash": version.design_hash}
    )
    decision = store.save_decision(
        ResearcherDecision(id="D1", run_id=run, binding=binding, problem="Choose sampling")
    )
    cutoff = store.events(run)[-1].event_id
    store.save_decision(decision.model_copy(update={"revision": 2, "problem": "Changed question"}))
    historical = store.publication_snapshot(run, event_id=cutoff)
    assert historical.decisions[0].revision == 1
    assert historical.decisions[0].problem == "Choose sampling"
    assert historical.version.design_hash == version.design_hash
    assert store.publication_snapshot(run).decisions[0].revision == 2
    before = store.events(run)
    with pytest.raises(RuntimeError), store.transaction():
        store.save_decision(decision.model_copy(update={"revision": 3}))
        raise RuntimeError("Interrupted transaction")
    assert store.events(run) == before
    assert store.decision(run, "D1").revision == 2
    store.close()
