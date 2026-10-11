import json

import pytest
from conftest import design
from fakes import ScriptedJudge, ScriptedLLM, submit
from test_stage_pipeline import (
    inspection_response,
    normal_responses,
    review_responses,
    source_context,
    source_snapshot,
)
from test_workflow import chain

from experiment_design.config import RunConfig
from experiment_design.contracts import DecisionResponse, ReviewCohort
from experiment_design.decisions import respond
from experiment_design.output import export
from experiment_design.qualification import can_approve
from experiment_design.review import review_actions
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, advance, approve, start_run


def test_mixed_model_limitations_reach_pending_review_and_draft_publication(tmp_path):
    context = source_context()
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
            "chain": chain(),
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
                    {"design": draft.model_dump(mode="json"), "rationale": "Mixed-model draft"},
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
                inspection_response(),
                submit(
                    "submit_handoff",
                    {
                        "handoff_complete": False,
                        "risks": ["District SD and ICC remain unverified"],
                        "findings": [],
                        "evidence_refs": [],
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
            ]
        ),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    assessment = store.publication_snapshot(run).assessment
    assert assessment.review_status == "REVIEWABLE"
    assert not assessment.handoff_complete
    assert not assessment.execution_ready
    assert "District SD and ICC remain unverified" in assessment.blockers
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
    markdown = paths["experiment_design.md"].read_text(encoding="utf-8")
    data = json.loads(paths["experiment_design.json"].read_text(encoding="utf-8"))
    assert "Draft — not approved" in markdown
    assert "Not computed:" in markdown
    assert "District SD and ICC remain unverified" in markdown
    assert "Mixed-model power unsupported" in markdown
    assert data["provenance"]["approval"] is None
    assert data["computed"] and all(
        c["status"] == "not_computed" and c["power"] is None for c in data["computed"]
    )
    assert data["design"]["analysis"]["model"] == "Mixed model with class random intercept"
    assert next(i for i in data["issues"] if i["id"] == "I2")["status"] == "unresolved"
    assert store.decision(run, final.id) == final
    assert not store.approvals(run)
    assert store.run(run).stage_id == "S7"
    assert store.run(run).status == "REVIEWABLE"
    artifacts = json.loads(paths["stage_artifacts.json"].read_text(encoding="utf-8"))
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
        "experiment_design.md",
        "experiment_design.json",
        "manifest.json",
    ]
    readable = paths["stage_artifacts.md"].read_text(encoding="utf-8")
    assert "Unsupported mixed-model planning" in readable
    assert "S1" in readable and "S7" in readable and "AWAITING_RESEARCHER" in readable
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
    data = json.loads(historical["experiment_design.json"].read_bytes())
    assert not any(d["purpose"] == "final_review" for d in data["decisions"])
    assert store.run(run).stage_id == "S6"
    assert not any(
        e.type == "stage.result" and e.data["stage_id"] == "S7" for e in store.events(run)
    )
    export(store, run, tmp_path / "current")
    assert store.run(run).stage_id == "S7"
    assert store.run(run).status == "REVIEWABLE" and not store.approvals(run)
    store.close()


def test_pending_final_review_does_not_block_scientific_response_reconciliation(tmp_path):
    from experiment_design.contracts import ResearcherDecision
    from experiment_design.runtime.llm import LLMError

    def understood(messages, tools):
        brief = json.loads(messages[1]["content"].split("\n")[-1])
        decision = ResearcherDecision(
            id="margin-review",
            run_id=brief["run_id"],
            binding=brief["binding"],
            problem="Review planning margin",
            affected_paths=["/claim/margin"],
        )
        return submit(
            "submit_understanding",
            {
                "context": source_context().model_dump(mode="json"),
                "decisions": [decision.model_dump(mode="json")],
            },
        )

    responses = normal_responses()
    responses[0] = understood
    store = Store(tmp_path / "reconcile.db")
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge(
            [
                {"next_action": "CONTINUE"},
                {"next_action": "REVIEW"},
                {"next_action": "QUALIFY"},
            ]
        ),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    final = next(d for d in store.decisions(run) if d.purpose == "final_review")
    assert store.publication_snapshot(run).assessment.review_status == "NEEDS_INPUT"
    export(store, run, tmp_path / "draft")
    decision = store.decision(run, "margin-review")
    respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=decision.revision,
            action="PROPOSE_ALTERNATIVE",
            text="Use margin 2 points",
            researcher="PI",
            response_id="new-margin",
        ),
    )
    deps.llm = ScriptedLLM(
        [
            submit(
                "submit_interpretation",
                {
                    "authorized": True,
                    "ambiguous": False,
                    "patch": {
                        "ops": [{"path": "/claim/margin", "value": 2}],
                        "rationale": "Researcher margin response",
                    },
                    "explanation": "Apply exact researcher choice",
                },
            ),
            LLMError("understanding provider interrupted during reconciliation"),
        ]
    )
    with pytest.raises(LLMError, match="during reconciliation"):
        advance(run, deps)
    assert store.run(run).stage_id == "S2"
    assert store.version(run).design.claim.margin.value == 2
    assert store.decision(run, decision.id).application["response_id"] == "new-margin"
    assert store.decision(run, final.id) == final
    assert store.publication_snapshot(run).assessment is None
    assert not store.approvals(run)
    store.close()


def test_review_counters_cannot_replace_a_published_independent_cohort(tmp_path):
    store = Store(tmp_path / "no-cohort.db")
    deps = Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig())
    run = start_run(source_snapshot(), deps, {})
    store.save_context(run, source_context())
    store.add_version(run, design(), author="design", parent=None)
    store.update_run(run, stage_id="S4", reviewed_version=1, reviews_done=1)
    assert "QUALIFY" not in review_actions(store, run, deps.run)
    assert "REVIEW" in review_actions(store, run, deps.run)
    store.close()


def test_jev_receives_review_progress_and_bound_computation_limitations(tmp_path):
    from experiment_design.contracts import CalculationRequest, Observation
    from experiment_design.science.power import calculate
    from experiment_design.workflow import jev_state

    store = Store(tmp_path / "progress.db")
    deps = Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig())
    run = start_run(source_snapshot(), deps, {})
    store.save_context(run, source_context())
    store.add_version(run, design(), author="design", parent=None)
    store.update_run(run, stage_id="S4", revisions_done=3, actions_done=5)
    result = calculate(
        CalculationRequest(binding=store.binding(run), scenario_id="base", design=design())
    )
    store.save_observation(
        run,
        Observation(
            id="power",
            task_id="review",
            binding=store.binding(run),
            tool_name="compute_power",
            arguments={},
            result={"value": [result.model_dump(mode="json")]},
            accepted=True,
            evidence_ref="tool:power",
        ),
    )
    state = jev_state(store, run, deps.run)
    assert state["progress"]["revisions_done"] == 3
    assert state["progress"]["reviews_done"] == 0
    assert state["progress"]["max_revisions"] == 3
    assert state["progress"]["cohorts"] == []
    assert state["calculations"][0]["status"] == "not_computed"
    assert state["calculations"][0]["reasons"]
    assert "draft" in state["objective"].lower()
    store.close()
