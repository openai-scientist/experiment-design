import json

import pytest

from experiment_design.config import RunConfig
from experiment_design.contracts import DecisionResponse, Patch, ResearcherDecision
from experiment_design.design.decisions import apply_decision, interpret, respond
from experiment_design.persistence.store import Store
from experiment_design.publication import export
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
    from experiment_design.design.decisions import respond
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


def test_historical_approved_version_excludes_later_answer(tmp_path):
    store = Store(tmp_path / "history.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    advance(run, deps)
    approve(store, run, 1, "PI")
    decision = store.save_decision(
        ResearcherDecision(
            id="later-margin",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Change margin",
            affected_paths=["/claim/margin"],
        )
    )
    answered = respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            text="Two points",
            researcher="PI",
            response_id="later",
        ),
    )
    context = store.context(run)
    patch = Patch(ops=[{"path": "/claim/margin", "value": 2}], rationale="PI answer")
    store.save_decision(
        interpret(answered, context, {"authorized": True, "patch": patch.model_dump()})
    )
    apply_decision(store, run, decision.id, context=context, patch=patch)
    old = store.publication_snapshot(run, version=1)
    assert old.approval and old.approval.approver == "PI"
    assert not any(d.id == decision.id for d in old.decisions)
    assert old.context.revision == 1 and old.version.design.claim.margin.value == 3
    store.close()
