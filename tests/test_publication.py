import json

import pytest
from fakes import ScriptedJudge, ScriptedLLM
from test_stage_pipeline import normal_responses, source_snapshot

from experiment_design.config import RunConfig
from experiment_design.output import export
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, advance, approve, start_run


def test_assessed_bundle_uses_bound_numbers_and_refuses_overwrite(tmp_path):
    store = Store(tmp_path / "study.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    advance(run, deps)
    draft = export(store, run, tmp_path / "draft")
    assert store.run(run).stage_id == "S7"
    assert "Draft" in draft["experiment_design.md"].read_text(encoding="utf-8")
    approve(store, run, 1, "researcher")
    approved = export(store, run, tmp_path / "approved")
    data = json.loads(approved["experiment_design.json"].read_bytes())
    manifest = json.loads(approved["manifest.json"].read_bytes())
    assert data["computed"][0]["id"] == store.publication_snapshot(run).calculations[0].id
    assert data["version"] == manifest["version"] == 1
    assert "Approved by researcher" in approved["experiment_design.md"].read_text(encoding="utf-8")
    assert store.run(run).status == "PUBLISHED"
    before = approved["experiment_design.json"].read_bytes()
    store.save_context(run, store.context(run).model_copy(update={"revision": 2}))
    with pytest.raises(ValueError, match="destination"):
        export(store, run, tmp_path / "approved")
    assert approved["experiment_design.json"].read_bytes() == before
    store.close()


def test_existing_complete_schema_two_assessment_preserves_exact_approval(tmp_path):
    from experiment_design.contracts import ReadinessAssessment

    store = Store(tmp_path / "existing.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    legacy = store.assessment(run).model_dump(mode="json", exclude={"execution_ready"})
    store.save_assessment(ReadinessAssessment.model_validate(legacy))
    approval = approve(store, run, 1, "PI")
    assert approval.design_hash == store.version(run).design_hash
    assert store.run(run).status == "APPROVED"
    store.close()


def test_sensitivity_and_old_context_do_not_support_current_publication(tmp_path):
    from conftest import package
    from test_stage_pipeline import source_snapshot, supported_design

    from experiment_design.contracts import (
        CalculationRequest,
        Observation,
        Quantity,
        ResearchContext,
    )
    from experiment_design.runtime.store import Store
    from experiment_design.science.power import calculate

    store = Store(tmp_path / "bound.db")
    run = store.create_staged_run(source_snapshot(), {})
    context = ResearchContext(
        id="context", revision=1, source_snapshot_id="source-1", package=package()
    )
    store.save_context(run, context)
    store.add_version(run, supported_design(), author="design", parent=None)
    binding = store.binding(run)
    for name, overrides in [
        ("canonical", {}),
        ("sensitivity", {"icc": Quantity(value=0.3, unit="", provenance="assumption")}),
    ]:
        result = calculate(
            CalculationRequest(
                binding=binding, scenario_id="base", design=supported_design(), overrides=overrides
            )
        )
        store.save_observation(
            run,
            Observation(
                id=name,
                task_id="power",
                binding=binding,
                tool_name="compute_power",
                arguments={
                    "overrides": {k: q.model_dump(mode="json") for k, q in overrides.items()}
                },
                result={"value": [result.model_dump(mode="json")]},
                accepted=True,
                evidence_ref=f"tool:{name}",
            ),
        )
    results = store.publication_snapshot(run).calculations
    assert len(results) == 1 and results[0].normalized_inputs["icc"] == 0.1
    store.save_context(run, context.model_copy(update={"revision": 2}))
    assert store.publication_snapshot(run).calculations == []
    store.close()


def test_interrupted_export_has_no_complete_manifest(tmp_path, monkeypatch):
    from pathlib import Path

    from test_decisions import decision_state

    store, run, _, _ = decision_state(tmp_path)
    original = Path.write_bytes

    def fail_json(path, data):
        if path.name == "experiment_design.json":
            raise OSError("disk unavailable")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_json)
    destination = tmp_path / "bundle"
    with pytest.raises(OSError, match="disk unavailable"):
        export(store, run, destination)
    assert not destination.exists()
    assert list(tmp_path.glob(".edp-*")) == []
    assert not any(e.type == "publication" for e in store.events(run))
    store.close()


def test_publication_resume_after_file_commit(tmp_path, monkeypatch):
    from test_decisions import decision_state

    store, run, _, _ = decision_state(tmp_path)
    original = store.record_publication

    def interrupted(*args):
        raise RuntimeError("journal interrupted")

    monkeypatch.setattr(store, "record_publication", interrupted)
    destination = tmp_path / "bundle"
    with pytest.raises(RuntimeError, match="journal interrupted"):
        export(store, run, destination)
    before = {p.name: p.read_bytes() for p in destination.iterdir()}
    monkeypatch.setattr(store, "record_publication", original)
    export(store, run, destination)
    export(store, run, destination)
    assert {p.name: p.read_bytes() for p in destination.iterdir()} == before
    assert len([e for e in store.events(run) if e.type == "publication"]) == 1
    assert store.version(run).version == 1 and store.approvals(run) == []
    store.close()
