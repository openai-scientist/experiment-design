import json

import pytest

from experiment_design.config import RunConfig
from experiment_design.output import export
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, advance, approve, start_run
from tests.support.providers import ScriptedJudge, ScriptedLLM
from tests.support.scenarios import normal_responses, source_snapshot


@pytest.mark.parametrize(
    "state,publication,approval",
    [
        ("unqualified", "draft", "not_requested"),
        ("blocked", "blocked", "pending"),
        ("pending", "draft", "pending"),
        ("held", "draft", "held"),
        ("rejected", "draft", "rejected"),
        ("approved", "approved", "approved"),
    ],
)
def test_manifest_status_comes_from_frozen_scientific_snapshot(
    tmp_path, state, publication, approval
):
    from experiment_design.contracts import DecisionResponse
    from experiment_design.decisions import respond
    from tests.support.providers import submit

    responses = normal_responses()
    if state == "blocked":
        responses[2] = submit(
            "submit_findings",
            {
                "summary": "Material gap",
                "findings": [
                    {
                        "severity": "MAJOR",
                        "paths": ["/protocol/steps"],
                        "mechanism": "The delivery sequence has an unresolved ambiguity",
                        "consequence": "Observations may reflect inconsistent exposure",
                        "basis": "reasoning",
                        "evidence_refs": ["S1"],
                    }
                ],
            },
        )
    store = Store(tmp_path / "status.db")
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge(
            [
                {"next_action": "REVIEW"},
                {"next_action": "QUALIFY"},
                *([{"next_action": "REVIEW_PLAN"}] if state == "blocked" else []),
            ]
        ),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    if state == "unqualified":
        from experiment_design.workflow import run_stage

        store.commit_stage(run, run_stage(run, "S1", deps), "S2")
        for stage in ("S2", "S3"):
            run_stage(run, stage, deps)
    else:
        assert advance(run, deps) == "REVIEWABLE"
    if state in {"held", "rejected"}:
        final = store.decisions(run)[-1]
        respond(
            store,
            run,
            final.id,
            DecisionResponse(
                revision=final.revision,
                action="HOLD" if state == "held" else "REJECT",
                text="Researcher review response",
                researcher="PI",
                response_id="status-response",
            ),
        )
        if state == "rejected":
            assert advance(run, deps) == "INCONCLUSIVE"
    if state == "approved":
        approve(store, run, 1, "PI")
    paths = export(store, run, tmp_path / "bundle")
    manifest = json.loads(paths["manifest.json"].read_bytes())
    payload = json.loads(paths["exp_plan.json"].read_bytes())
    assert manifest["publication_status"] == payload["publication_status"] == publication
    assert manifest["approval_status"] == payload["approval_status"] == approval
    assert manifest["binding"] == payload["binding"]
    before = {name: path.read_bytes() for name, path in paths.items()}
    context = store.context(run).model_copy(deep=True)
    context.revision += 1
    store.save_context(run, context)
    historical = export(store, run, tmp_path / "historical", version=1)
    assert {name: path.read_bytes() for name, path in historical.items()} == before
    store.close()


def test_assessed_bundle_uses_bound_numbers_and_refuses_overwrite(tmp_path):
    store = Store(tmp_path / "study.db")
    from tests.support.scenarios import inspection_response

    responses = normal_responses()
    responses.insert(2, inspection_response())
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    advance(run, deps)
    draft = export(store, run, tmp_path / "draft")
    assert store.run(run).stage_id == "S7"
    assert "Draft" in draft["exp_plan.md"].read_text(encoding="utf-8")
    approve(store, run, 1, "researcher")
    approved = export(store, run, tmp_path / "approved")
    data = json.loads(approved["exp_plan.json"].read_bytes())
    manifest = json.loads(approved["manifest.json"].read_bytes())
    assert data["calculations"][0]["id"] == store.publication_snapshot(run).calculations[0].id
    assert data["version"] == manifest["version"] == 1
    assert "Approved by researcher" in approved["exp_plan.md"].read_text(encoding="utf-8")
    assert store.run(run).status == "PUBLISHED"
    before = approved["exp_plan.json"].read_bytes()
    store.save_context(run, store.context(run).model_copy(update={"revision": 2}))
    with pytest.raises(ValueError, match="destination"):
        export(store, run, tmp_path / "approved")
    assert approved["exp_plan.json"].read_bytes() == before
    store.close()


def test_historical_readiness_does_not_promote_to_active_plan_approval(tmp_path):
    from experiment_design.contracts import ReadinessAssessment
    from experiment_design.qualification import can_approve

    store = Store(tmp_path / "existing.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    advance(run, deps)
    legacy = store.assessment(run).model_dump(mode="json", exclude={"plan_complete"})
    legacy["handoff_complete"] = True
    old = ReadinessAssessment.model_validate(legacy)
    assert old.execution_ready is True and old.plan_complete is None
    store.save_assessment(old)
    assert can_approve(store.publication_snapshot(run))
    with pytest.raises(ValueError, match="complete reviewed plan"):
        approve(store, run, 1, "PI")
    assert store.approvals(run) == []
    store.close()


def test_sensitivity_and_old_context_do_not_support_current_publication(tmp_path):
    from experiment_design.contracts import (
        CalculationRequest,
        Observation,
        Quantity,
        ResearchContext,
    )
    from experiment_design.runtime.store import Store
    from experiment_design.science.power import calculate
    from tests.support.builders import package
    from tests.support.scenarios import source_snapshot, supported_design

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
                calculator_id=(supported_design()).analysis.method_id,
                binding=binding,
                scenario_id="base",
                design=supported_design(),
                overrides=overrides,
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

    from tests.support.scenarios import decision_state

    store, run, _, _ = decision_state(tmp_path)
    original = Path.write_bytes

    def fail_json(path, data):
        if path.name == "exp_plan.json":
            raise OSError("disk unavailable")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_json)
    destination = tmp_path / "bundle"
    with pytest.raises(OSError, match="disk unavailable"):
        export(store, run, destination)
    assert not destination.exists()
    assert list(tmp_path.glob(".exp-plan-*")) == []
    assert not any(e.type == "publication" for e in store.events(run))
    store.close()


def test_publication_resume_after_file_commit(tmp_path, monkeypatch):
    from tests.support.scenarios import decision_state

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
