import json

import pytest

from experiment_design.config import RunConfig
from experiment_design.contracts import DecisionResponse, ReviewCohort
from experiment_design.design.approval import can_approve
from experiment_design.design.decisions import respond
from experiment_design.persistence.store import Store
from experiment_design.publication import export
from experiment_design.workflow import Deps, advance, approve, start_run
from tests.support.builders import design
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit
from tests.support.scenarios import (
    normal_responses,
    plan_body,
    review_responses,
    source_context,
    source_snapshot,
)


def test_mixed_model_limitations_reach_pending_review_and_draft_publication(tmp_path):
    context = source_context(method="Mixed model with class random intercept")
    context.unknowns = ["District SD and ICC remain unverified"]
    draft = design()
    draft.scenarios[0].sd.provenance = "assumption"
    draft.scenarios[0].sd.source_ref = None
    draft.scenarios[0].sd.source_span = None
    draft.sampling.cluster.icc.provenance = "assumption"
    draft.sampling.cluster.icc.source_ref = None
    draft.sampling.cluster.icc.source_span = None
    reviews = review_responses()
    reviews[0] = submit(
        "submit_findings",
        {
            "summary": "Unsupported mixed-model planning",
            "findings": [
                {
                    "severity": "MINOR",
                    "paths": ["/protocol/steps"],
                    "mechanism": "Record delivery logs for reproducibility",
                    "consequence": "Delivery fidelity remains a planning detail",
                    "basis": "reasoning",
                    "evidence_refs": ["S1"],
                },
                {
                    "severity": "MAJOR",
                    "paths": ["/analysis/model", "/scenarios", "/sampling/cluster"],
                    "mechanism": "Mixed-model power unsupported; district SD and ICC unverified",
                    "consequence": "Execution requires validated nuisance parameters and power",
                    "basis": "reasoning",
                    "evidence_refs": ["S1"],
                },
            ],
        },
    )
    store = Store(tmp_path / "draft.db")
    deps = Deps(
        store,
        ScriptedLLM(
            [
                submit(
                    "submit_understanding",
                    {"context": context.model_dump(mode="json"), "decisions": []},
                ),
                submit(
                    "submit_design",
                    {"design": plan_body(draft), "summary": "Mixed-model draft"},
                ),
                *reviews,
                submit(
                    "submit_revision",
                    {
                        "patch": {
                            "ops": [],
                            "rationale": "Retain explicit unsupported planning limitation",
                        },
                        "rebuttals": [
                            {
                                "issue_id": "I2",
                                "argument": (
                                    "District SD/ICC are assumptions; mixed-model power needs "
                                    "external validation before execution"
                                ),
                                "evidence_refs": ["S1"],
                            }
                        ],
                    },
                ),
                submit(
                    "submit_verdicts",
                    {
                        "verdicts": [
                            {
                                "issue_id": "I2",
                                "verdict": "uncertain",
                                "grounds": (
                                    "Acknowledging missing district evidence does not establish "
                                    "power or validate SD/ICC"
                                ),
                                "evidence_refs": ["S1"],
                            }
                        ],
                        "regressions": [],
                    },
                ),
            ]
        ),
        ScriptedJudge(
            [
                {"next_action": "REVIEW"},
                {"next_action": "REVISE"},
                {"next_action": "VERIFY"},
                {"I2": "unresolved"},
                {"next_action": "QUALIFY"},
                {"next_action": "REVIEW_PLAN"},
            ]
        ),
        RunConfig(),
    )
    run = start_run(source_snapshot(method="Mixed model with class random intercept"), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    assessment = store.publication_snapshot(run).assessment
    assert assessment.review_status == "REVIEWABLE"
    assert not assessment.plan_complete
    assert any("Mixed-model power unsupported" in b for b in assessment.blockers)
    assert can_approve(store.publication_snapshot(run))
    final = next(d for d in store.decisions(run) if d.purpose == "final_review")
    assert final.status == "AWAITING_RESEARCHER" and not final.responses
    assert final.dependencies == [assessment.id]
    before = store.events(run)
    with pytest.raises(ValueError):
        approve(store, run, 1, "PI")
    assert store.events(run) == before
    paths = export(store, run, tmp_path / "edp")
    markdown = paths["exp_plan.md"].read_text(encoding="utf-8")
    data = json.loads(paths["exp_plan.json"].read_text(encoding="utf-8"))
    assert "Draft" in markdown
    assert "District SD and ICC remain unverified" in markdown
    assert "Mixed-model power unsupported" in markdown
    assert data["approval"] is None
    assert data["calculations"] == []
    assert data["design"]["analysis"]["model"] == "Mixed model with class random intercept"
    assert next(i for i in data["issues"] if i["id"] == "I2")["status"] == "unresolved"
    assert store.decision(run, final.id) == final
    assert not store.approvals(run)
    assert store.run(run).stage_id == "S7"
    assert store.run(run).status == "REVIEWABLE"
    artifacts = store.stage_artifacts(run, store.publication_snapshot(run))
    assert artifacts["binding"]["design_version"] == 1
    assert set(artifacts["stages"]) == {"S1", "S2", "S3", "S4", "S5", "S6", "S7"}
    assert artifacts["stages"]["S1"]["source"]["artifacts"][0]["text"]
    assert artifacts["stages"]["S2"]["context"]["unknowns"] == [
        "District SD and ICC remain unverified"
    ]
    assert artifacts["stages"]["S3"]["version"]["design_hash"] == data["binding"]["design_hash"]
    assert {t["agent_id"] for t in artifacts["stages"]["S4"]["submissions"]} == {
        "methodologist",
        "skeptic",
        "design",
        "verifier",
    }
    assert all(
        t["binding"]["design_version"] == 1 and t["result"]["findings_or_changes"]["summary"]
        for t in artifacts["stages"]["S4"]["submissions"]
        if t["agent_id"] in {"methodologist", "skeptic"}
    )
    assert artifacts["stages"]["S5"]["assessment"]["id"] == assessment.id
    assert artifacts["stages"]["S6"]["decisions"][-1]["responses"] == []
    assert artifacts["stages"]["S7"]["files"] == [
        "exp_plan.md",
        "exp_plan.json",
        "manifest.json",
    ]
    stages = [e.data for e in store.events(run) if e.type == "stage.result"]
    assert {s["stage_id"] for s in stages if s["output_refs"]} == {
        "S1",
        "S2",
        "S3",
        "S4",
        "S5",
        "S6",
        "S7",
    }
    cohorts = store._artifact_records(run, "cohort", ReviewCohort)
    assert len(cohorts) == 1 and cohorts[0].published_event_id is not None
    assert {store.task(t).task.stage_id for t in cohorts[0].member_task_ids.values()} == {"S4"}
    assert all(store.task(t).status == "completed" for t in cohorts[0].member_task_ids.values())
    events = store.events(run)
    export(store, run, tmp_path / "edp")
    assert store.events(run) == events
    with pytest.raises(ValueError):
        approve(store, run, 1, "PI")
    store.close()


def test_researcher_can_request_changes_after_draft_publication(tmp_path):
    store = Store(tmp_path / "feedback.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    final = store.decisions(run)[-1]
    export(store, run, tmp_path / "draft")
    assert store.run(run).stage_id == "S7"
    respond(
        store,
        run,
        final.id,
        DecisionResponse(
            revision=final.revision,
            action="REQUEST_CHANGES",
            text="Specify SD validation protocol",
            researcher="PI",
            response_id="change-after-draft",
        ),
    )
    deps.judge = ScriptedJudge([{"next_action": "STOP"}])
    assert advance(run, deps) == "INCONCLUSIVE"
    assert store.run(run).stage_id == "S4"
    assert store.decision(run, final.id).application["response_id"] == "change-after-draft"
    store.close()


def test_historical_export_cannot_complete_an_unpresented_final_review(tmp_path):
    store = Store(tmp_path / "historical.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    historical = export(store, run, tmp_path / "historical", version=1)
    data = json.loads(historical["exp_plan.json"].read_bytes())
    assert not any(d["purpose"] == "final_review" for d in data["decisions"])
    assert store.run(run).stage_id == "S6"
    assert not any(
        e.type == "stage.result" and e.data["stage_id"] == "S7" for e in store.events(run)
    )
    export(store, run, tmp_path / "current")
    assert store.run(run).stage_id == "S7"
    assert store.run(run).status == "REVIEWABLE" and not store.approvals(run)
    store.close()


def test_final_clarification_recovers_without_revision_or_approval(tmp_path, monkeypatch, capsys):
    from experiment_design import cli
    from experiment_design.contracts import Observation
    from experiment_design.workflow import WorkflowError
    from tests.support.providers import tool_call

    store = Store(tmp_path / "clarify.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge(
            [
                {"next_action": "REVIEW"},
                {"next_action": "QUALIFY"},
            ]
        ),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    final = store.decisions(run)[-1]
    version = store.version(run)
    assessment = store.assessment(run)
    usage = store.usage(run).llm_tokens
    config = tmp_path / "cli.yaml"
    config.write_text(
        "llm:\n  base_url: https://provider.example/v1\n  model: test\nstore: "
        + str(tmp_path / "clarify.db")
        + "\n",
        encoding="utf-8",
    )
    assert cli.main(["--config", str(config), "status", run]) == 0
    assert "--action CLARIFY" in capsys.readouterr().out
    assert (
        cli.main(
            [
                "--config",
                str(config),
                "decide",
                run,
                final.id,
                "--revision",
                str(final.revision),
                "--action",
                "CLARIFY",
                "--text",
                "Explain the recruitment assumptions",
                "--by",
                "PI",
            ]
        )
        == 0
    )
    question = store.decision(run, final.id).responses[-1]
    with pytest.raises(WorkflowError, match="final review"):
        approve(store, run, version.version, "PI")

    def inspect_plan(messages, tools):
        assert "Explain the recruitment assumptions" in messages[1]["content"]
        offered = {t["function"]["name"] for t in tools}
        assert "submit_clarification" in offered and "submit_revision" not in offered
        assert "compute_power" not in offered and "check_patch" not in offered
        return tool_call("read_design", {}, call_id="clarification-inspection")

    def explain(messages, tools):
        ref = json.loads(messages[-1]["content"])["evidence_ref"]
        return submit(
            "submit_clarification",
            {
                "explanation": (
                    "Recruitment follows the recorded class allocation; "
                    "nuisance values remain assumptions."
                ),
                "evidence_refs": [ref],
            },
        )

    deps.llm = ScriptedLLM([inspect_plan, explain])
    original_save = store.save_decision

    def interrupted(decision):
        if question.response_id in decision.interpretation.get("clarifications", {}):
            raise RuntimeError("answer persistence interrupted")
        return original_save(decision)

    monkeypatch.setattr(store, "save_decision", interrupted)
    with pytest.raises(RuntimeError, match="answer persistence interrupted"):
        advance(run, deps)
    assert store.decision(run, final.id).status == "ANSWERED"
    monkeypatch.setattr(store, "save_decision", original_save)
    deps.llm = ScriptedLLM([])
    assert advance(run, deps) == "REVIEWABLE"
    assert deps.llm.calls == []
    answered = store.decision(run, final.id)
    answer = answered.interpretation["clarifications"][question.response_id]
    assert "remain assumptions" in answer["explanation"]
    ref = answer["evidence_refs"][0]
    observation = next(
        o for o in store._artifact_records(run, "observation", Observation) if o.evidence_ref == ref
    )
    assert observation.accepted and observation.binding.design_hash == version.design_hash
    assert answered.status == "AWAITING_RESEARCHER" and answered.application == {}
    assert store.version(run).version == version.version
    assert store.version(run).design_hash == version.design_hash
    assert store.assessment(run).id == assessment.id
    assert store.issues(run) == [] and store.approvals(run) == []
    assert store.run(run).stage_id == "S6" and store.run(run).revisions_done == 0
    assert store.usage(run).llm_tokens > usage
    before = store.events(run)
    with pytest.raises(ValueError, match="question"):
        respond(
            store,
            run,
            final.id,
            DecisionResponse(
                revision=answered.revision,
                action="CLARIFY",
                text=" ",
                researcher="PI",
                response_id="empty",
            ),
        )
    assert store.events(run) == before

    respond(
        store,
        run,
        final.id,
        DecisionResponse(
            revision=answered.revision,
            action="CLARIFY",
            text="Explain the stopping policy",
            researcher="PI",
            response_id="second-question",
        ),
    )
    deps.llm = ScriptedLLM(
        [
            submit(
                "submit_clarification",
                {
                    "explanation": "The analysis and consent stop condition remain unchanged.",
                    "evidence_refs": ["S1"],
                },
            )
        ]
    )
    assert advance(run, deps) == "REVIEWABLE"
    answered = store.decision(run, final.id)
    assert set(answered.interpretation["clarifications"]) == {
        question.response_id,
        "second-question",
    }
    paths = export(store, run, tmp_path / "clarified-draft")
    text = paths["exp_plan.md"].read_text(encoding="utf-8")
    assert answer["explanation"] in text and "Explain the stopping policy" in text
    data = json.loads(paths["exp_plan.json"].read_bytes())
    assert data["approval"] is None and data["approval_status"] == "pending"
    assert any(o["evidence_ref"] == ref for o in data["observations"])
    approve(store, run, version.version, "PI")
    approved = export(store, run, tmp_path / "clarified-approved")
    assert json.loads(approved["manifest.json"].read_bytes())["approval_status"] == "approved"
    assert store.run(run).revisions_done == 0 and store.version(run).version == version.version
    store.close()
