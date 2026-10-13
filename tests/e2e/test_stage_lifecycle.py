import pytest

from experiment_design.config import RunConfig
from experiment_design.persistence.store import Store
from experiment_design.workflow import Deps, WorkflowError, advance, approve, start_run
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit, tool_call
from tests.support.scenarios import (
    inspection_response,
    normal_responses,
    plan_body,
    review_responses,
    source_context,
    source_snapshot,
    supported_design,
)


def test_stages_reach_exact_researcher_review_and_publication_wait(tmp_path):
    store = Store(tmp_path / "pipeline.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    assert store.run(run).stage_id == "S6"
    assert [
        e.data["stage_id"]
        for e in store.events(run)
        if e.type == "stage.result" and e.data["status"] == "COMPLETE"
    ] == ["S1", "S2", "S3", "S4", "S5"]
    approve(store, run, 1, "researcher")
    assert store.run(run).stage_id == "S7"
    assert [
        e.data["stage_id"]
        for e in store.events(run)
        if e.type == "stage.result" and e.data["status"] == "COMPLETE"
    ] == ["S1", "S2", "S3", "S4", "S5", "S6"]
    assert store.assessment(run).plan_complete
    store.backup(tmp_path / "backup.db")
    reopened = Store(tmp_path / "backup.db")
    assert (
        reopened.source_snapshot(run).artifacts[0].sha256 == source_snapshot().artifacts[0].sha256
    )
    assert reopened.context(run) == store.context(run)
    assert reopened.version(run).design_hash == store.version(run).design_hash
    reopened.close()
    store.close()


def test_proposed_margin_cannot_reach_final_approval(tmp_path):
    context = source_context()
    context.field_annotations["margin"].status = "agent-proposed"
    responses = normal_responses()
    responses[0] = submit(
        "submit_understanding", {"context": context.model_dump(mode="json"), "decisions": []}
    )
    store = Store(tmp_path / "proposed.db")
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "NEEDS_RESEARCHER"
    assert not store.assessment(run).plan_complete
    assert any("margin" in reason for reason in store.assessment(run).blockers)
    with pytest.raises((ValueError, WorkflowError)):
        approve(store, run, 1, "PI")
    assert not store.approvals(run)
    store.close()


def test_final_request_changes_is_a_decision_and_returns_to_refinement(tmp_path):
    from experiment_design.contracts import DecisionResponse
    from experiment_design.design.decisions import respond

    store = Store(tmp_path / "review.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    final = store.decisions(run)[-1]
    respond(
        store,
        run,
        final.id,
        DecisionResponse(
            revision=1,
            action="REQUEST_CHANGES",
            text="Describe classroom assignment reproducibly",
            researcher="PI",
            response_id="change",
        ),
    )
    deps.llm = ScriptedLLM([])
    deps.judge = ScriptedJudge([{"next_action": "STOP"}])
    assert advance(run, deps) == "INCONCLUSIVE"
    recorded = store.decision(run, final.id)
    assert recorded.status == "APPLIED"
    assert recorded.application["response_id"] == "change"
    assert store.run(run).stage_id == "S4"
    assert any(
        i.mechanism == "Describe classroom assignment reproducibly" for i in store.issues(run)
    )
    assert any(
        e.type == "stage.result"
        and e.data.get("target") == "S4"
        and e.data.get("originating_ref") == final.id
        for e in store.events(run)
    )
    store.close()


def test_researcher_reconciliation_flows_through_scientific_review_to_publication(tmp_path):
    import json

    from experiment_design.contracts import Assumption, DecisionResponse
    from experiment_design.design.decisions import respond
    from experiment_design.publication import export

    store = Store(tmp_path / "reconciliation.db")
    context = source_context()
    context.unknowns = ["Estimand requires researcher clarification"]
    draft = supported_design()
    limitation = "Planning assumes zero true effect; transport beyond this district is unvalidated."
    draft.assumptions = [Assumption(id="planning", statement=limitation)]
    draft.handoff = ["Ask researcher to clarify the estimand"]

    def method_review(messages, tools):
        observations = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        assert observations[0]["result"]["analysis"]["model"] == "Pooled t test of cluster means"
        assert observations[1]["result"][0]["status"] == "computed"
        ref = observations[0]["evidence_ref"]
        return submit(
            "submit_findings",
            {
                "summary": "Inspected class-mean SAP and missingness protocol",
                "findings": [
                    {
                        "severity": "MAJOR",
                        "paths": ["/measurement/missingness"],
                        "mechanism": "Attrition reporting lacks ITT missingness handling",
                        "consequence": "Implementers could drop randomized classes",
                        "basis": "reasoning",
                        "evidence_refs": [ref],
                    }
                ],
            },
        )

    def proposal(messages, tools):
        return submit(
            "submit_design",
            {
                "design": plan_body(draft),
                "summary": "Supported planning draft",
                "decisions": [
                    {
                        "problem": "Clarify ITT estimand",
                        "affected_paths": ["/claim/estimand"],
                        "dependencies": ["missingness protocol"],
                    }
                ],
            },
        )

    cfg = RunConfig()
    first = Deps(
        store,
        ScriptedLLM(
            [
                submit("submit_understanding", {"context": context.model_dump(mode="json")}),
                proposal,
                inspection_response(),
                method_review,
                review_responses()[1],
            ]
        ),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "ASK_RESEARCHER"}]),
        cfg,
    )
    run = start_run(source_snapshot(), first, {})
    assert advance(run, first) == "NEEDS_RESEARCHER"
    decision = next(d for d in store.decisions(run) if d.purpose == "scientific")
    estimand = "ITT difference in class means among all randomized classes"
    respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=decision.revision,
            action="PROPOSE_ALTERNATIVE",
            text=estimand,
            researcher="PI",
            response_id="clarified-itt",
        ),
    )

    def reconciled_understanding(messages, tools):
        applied = store.decision(run, decision.id)
        assert applied.application["version"] == 2
        assert applied.verification["status"] == "pending"
        assert "clarified-itt" in messages[1]["content"]
        updated = store.context(run).model_copy(deep=True)
        updated.revision += 1
        updated.unknowns = []
        return submit(
            "submit_understanding",
            {
                "context": updated.model_dump(mode="json"),
                "resolved_unknowns": {decision.id: ["Estimand requires researcher clarification"]},
            },
        )

    def revise(messages, tools):
        applied = store.decision(run, decision.id)
        assert applied.reconciliation["unknown_resolutions"] == [
            {
                "decision_id": decision.id,
                "response_id": "clarified-itt",
                "context_revision": 3,
                "unknowns": ["Estimand requires researcher clarification"],
            }
        ]
        assert "clarified-itt" in messages[1]["content"]
        assert store.version(run).design.claim.estimand.value == estimand
        return submit(
            "submit_revision",
            {
                "patch": {
                    "ops": [
                        {"path": "/sampling/inclusion", "value": "All randomized Grade-7 classes"},
                        {
                            "path": "/measurement/missingness",
                            "value": "Retain all randomized classes; prespecify imputation "
                            "and report attrition by arm",
                        },
                        {
                            "path": "/analysis/sensitivity",
                            "value": [
                                "Report missingness sensitivity separately from primary "
                                "class-mean analysis"
                            ],
                        },
                        {
                            "path": "/protocol/steps",
                            "value": [
                                "Randomize classes",
                                "Deliver weekly feedback",
                                "Collect standardized end-of-term scores",
                                "Retain all randomized classes and apply prespecified "
                                "missingness handling",
                            ],
                        },
                        {
                            "path": "/handoff",
                            "value": [
                                "Implement confirmed ITT estimand and class-mean SAP",
                                limitation,
                            ],
                        },
                    ],
                    "rationale": "Propagate confirmed estimand through implementation",
                    "addresses": [i.id for i in store.issues(run) if i.status == "open"],
                }
            },
        )

    def verify_calls(messages, tools):
        focus = json.loads(messages[1]["content"].rsplit("\nVerification focus: ", 1)[1])
        assert all(
            c["addressed_version"] == 3 and c["parent_version"] == 2 for c in focus["comparisons"]
        )
        clarification = focus["researcher_clarifications"][0]
        assert clarification["application"]["version"] == 2
        assert clarification["response"]["response_id"] == "clarified-itt"
        assert clarification["reconciliation"]["unknown_resolutions"][0]["context_revision"] == 3
        response = tool_call("read_diff", {"from_version": 2, "to_version": 3})
        response.tool_calls.extend(inspection_response().tool_calls)
        response.tool_calls.extend(
            tool_call(
                "compute_power",
                {
                    "calculator_id": "cluster_equal_size_t",
                    "overrides": {
                        "sd": {
                            "value": 12,
                            "unit": "points",
                            "provenance": "assumption",
                            "note": "Sensitivity only",
                        },
                    },
                },
            ).tool_calls
        )
        return response

    def verdicts(messages, tools):
        observations = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        assert observations[-2]["result"][0]["status"] == "computed"
        return submit(
            "submit_verdicts",
            {
                "verdicts": [
                    {
                        "issue_id": i.id,
                        "verdict": "resolved",
                        "grounds": "Inspected dependent protocol diff, confirmed ITT "
                        "and current canonical calculation",
                        "evidence_refs": [observations[-2]["evidence_ref"]],
                    }
                    for i in store.issues(run)
                    if i.status == "addressed"
                ],
                "regressions": [],
            },
        )

    class ReconciliationJudge:
        def decide(self, state, questions):
            if "next_action" in questions:
                action = (
                    "VERIFY"
                    if any(i.status == "addressed" for i in store.issues(run))
                    else "REVIEW"
                    if store.run(run).reviewed_version < 3
                    else "QUALIFY"
                )
                return ScriptedJudge([{"next_action": action}]).decide(state, questions)
            return ScriptedJudge([{key: "resolved" for key in questions}]).decide(state, questions)

    second = Deps(
        store,
        ScriptedLLM(
            [
                submit(
                    "submit_interpretation",
                    {
                        "authorized": True,
                        "ambiguous": False,
                        "patch": {
                            "ops": [{"path": "/claim/estimand", "value": estimand}],
                            "rationale": "Recorded PI answer",
                        },
                        "explanation": "Implement exact reply",
                    },
                ),
                reconciled_understanding,
                inspection_response(),
                revise,
                verify_calls,
                verdicts,
                inspection_response(),
                *review_responses(),
                inspection_response(),
            ]
        ),
        ReconciliationJudge(),
        cfg,
    )
    assert advance(run, second) == "REVIEWABLE"
    final = store.version(run)
    assert final.version == 3
    assert (
        final.design.claim.estimand.value
        == store.context(run).package.inference.estimand
        == estimand
    )
    assert final.design.measurement.primary_metric.name == final.design.claim.primary_outcome.value
    assert final.design.measurement.primary_metric.unit == final.design.claim.outcome_unit.value
    assert final.design.handoff == [
        "Implement confirmed ITT estimand and class-mean SAP",
        limitation,
    ]
    assert final.design.assumptions[0].statement == limitation
    assert store.context(run).unknowns == []
    assert store.decision(run, decision.id).verification["status"] == "verified"
    snapshot = store.publication_snapshot(run)
    assert snapshot.calculations and all(
        c.status == "computed" and c.binding.design_version == 3 and c.binding.context_revision == 3
        for c in snapshot.calculations
    )
    observations = [
        e.data["record"]
        for e in store.events(run)
        if e.type == "artifact" and e.data["kind"] == "observation"
    ]
    sensitivity = [
        o
        for o in observations
        if o["tool_name"] == "compute_power" and o["arguments"].get("overrides")
    ]
    assert len(sensitivity) == 1
    assert sensitivity[0]["result"]["value"][0]["status"] == "computed"
    assert all(c.id != sensitivity[0]["result"]["value"][0]["id"] for c in snapshot.calculations)
    assert {e.agent_id for e in store.events(run) if e.type == "task.started"} >= {
        "understanding",
        "decision_interpretation",
        "design",
        "methodologist",
        "skeptic",
        "verifier",
    }
    approve(store, run, 3, "PI")
    paths = export(store, run, tmp_path / "publication")
    assert limitation in paths["exp_plan.md"].read_text(encoding="utf-8")
    assert store.run(run).stage_id == "S7"
    assert {
        e.data["stage_id"]
        for e in store.events(run)
        if e.type == "stage.result" and e.data["status"] == "COMPLETE"
    } == {"S1", "S2", "S3", "S4", "S5", "S6", "S7"}
    store.close()
