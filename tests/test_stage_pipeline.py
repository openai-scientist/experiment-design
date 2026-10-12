import pytest

from experiment_design.config import RunConfig
from experiment_design.runtime.llm import LLMError
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, WorkflowError, advance, approve, start_run
from tests.support.builders import package
from tests.support.history import create_historical_run
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit, tool_call
from tests.support.scenarios import (
    inspection_response,
    normal_responses,
    plan_body,
    review_responses,
    source_context,
    source_snapshot,
    supported_design,
    understanding_response,
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


def test_resume_between_reviews_keeps_committed_first_submission(tmp_path):
    store = Store(tmp_path / "pipeline.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()[:3] + [LLMError("skeptic interrupted")]),
        ScriptedJudge([{"next_action": "REVIEW"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    with pytest.raises(LLMError):
        advance(run, deps)
    assert store.issues(run) == []
    resumed = Deps(
        store,
        ScriptedLLM([review_responses()[1], *normal_responses()[4:]]),
        ScriptedJudge([{"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    assert advance(run, resumed) == "REVIEWABLE"
    assert len(resumed.llm.calls) == 1
    started = [
        e for e in store.events(run) if e.type == "task.started" and e.agent_id == "methodologist"
    ]
    assert len(started) == 1
    store.close()


@pytest.mark.parametrize("schema_version", [1, 2])
def test_legacy_resume_refused_without_mutation(tmp_path, schema_version):
    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import respond

    store = Store(tmp_path / "legacy.db")
    if schema_version == 1:
        run = create_historical_run(store, package(), {})
    else:
        run = store.create_staged_run(source_snapshot(), {})
        historical = store.run(run).model_copy(update={"plan_contract": None})
        store.connection.execute(
            "UPDATE runs SET data=? WHERE run_id=?", (historical.model_dump_json(), run)
        )
        store.save_decision(
            ResearcherDecision(
                id="old-question",
                run_id=run,
                binding=store.binding(run),
                status="AWAITING_RESEARCHER",
                problem="Old clarification",
                affected_paths=[],
            )
        )
    before = store.events(run)
    with pytest.raises(WorkflowError, match="schema|read/export only"):
        advance(run, Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig()))
    assert store.events(run) == before
    with pytest.raises(ValueError, match="read/export only"):
        respond(
            store,
            run,
            "old-question",
            DecisionResponse(
                revision=1,
                action="PROPOSE_ALTERNATIVE",
                text="New intent",
                researcher="PI",
                response_id="reply",
            ),
        )
    assert store.events(run) == before
    store.close()


def test_final_request_changes_is_a_decision_and_returns_to_refinement(tmp_path):
    from experiment_design.contracts import DecisionResponse
    from experiment_design.decisions import respond

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


def test_agent_decision_is_presented_against_committed_context(tmp_path):
    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import respond

    def understood(messages, tools):
        import json

        brief = json.loads(messages[-1]["content"].split("\n")[-1])
        context = source_context()
        decision = ResearcherDecision(
            id="missing-sap",
            run_id=brief["run_id"],
            binding=brief["binding"],
            problem="Confirm SAP",
            affected_paths=["/analysis/model"],
            dependencies=["synthesis"],
        )
        return submit(
            "submit_understanding",
            {
                "context": context.model_dump(mode="json"),
                "decisions": [decision.model_dump(mode="json")],
            },
        )

    store = Store(tmp_path / "clarification.db")
    deps = Deps(
        store,
        ScriptedLLM([understood, normal_responses()[1], *review_responses()]),
        ScriptedJudge([{"next_action": "ASK_RESEARCHER"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "NEEDS_RESEARCHER"
    decision = store.decision(run, "missing-sap")
    answered = respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            text="Use the specified SAP",
            researcher="PI",
            response_id="answer",
        ),
    )
    assert answered.status == "ANSWERED"
    assert decision.binding.context_revision == 1
    store.close()


def test_understanding_stage_commit_failure_keeps_accepted_framing_for_resume(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "atomic.db")
    deps = Deps(store, ScriptedLLM([understanding_response()]), ScriptedJudge([]), RunConfig())
    run = start_run(source_snapshot(), deps, {})
    commit = store.commit_stage

    def interrupt(run_id, result, target):
        if result.stage_id == "S2":
            raise RuntimeError("stage commit failed")
        return commit(run_id, result, target)

    monkeypatch.setattr(store, "commit_stage", interrupt)
    with pytest.raises(RuntimeError, match="stage commit"):
        advance(run, deps)
    assert store.context(run).revision == 1
    assert store.run(run).stage_id == "S2"
    assert store.usage(run).llm_calls == 1
    monkeypatch.setattr(store, "commit_stage", commit)
    resumed = Deps(
        store,
        ScriptedLLM(normal_responses()[1:]),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    assert advance(run, resumed) == "REVIEWABLE"
    assert len(resumed.llm.calls) == 3
    store.close()


def test_context_change_during_drafting_cannot_commit_a_stale_initial_design(tmp_path):
    from experiment_design.workflow import run_stage
    from tests.support.scenarios import staged

    store = Store(tmp_path / "stale-draft.db")
    run, _ = staged(store)
    context = source_context()
    store.save_context(run, context)
    store.update_run(run, stage_id="S3")

    def compose(messages, tools):
        changed = context.model_copy(deep=True)
        changed.revision += 1
        changed.package.hypothesis.method = "New researcher method"
        store.save_context(run, changed)
        return submit(
            "submit_design",
            {"design": plan_body(supported_design()), "summary": "Earlier context"},
        )

    deps = Deps(store, ScriptedLLM([compose]), ScriptedJudge([]), RunConfig())
    with pytest.raises(ValueError, match="stale"):
        run_stage(run, "S3", deps)
    assert store.version(run) is None
    assert store.context(run).package.hypothesis.method == "New researcher method"
    store.close()


def test_initial_draft_verification_receives_recorded_researcher_intent(tmp_path):
    import json

    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import apply_decision, interpret, respond
    from experiment_design.workflow import run_stage
    from tests.support.scenarios import staged

    store = Store(tmp_path / "initial-verification.db")
    run, _ = staged(store)
    context = source_context()
    store.save_context(run, context)
    decision = store.save_decision(
        ResearcherDecision(
            id="estimand",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Confirm estimand",
            affected_paths=["/context/package/inference/estimand"],
        )
    )
    text = "ITT mean score difference AI minus teacher among class-randomized pupils."
    answered = respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            text=text,
            researcher="PI",
            response_id="intent-answer",
        ),
    )
    store.save_decision(
        interpret(
            answered,
            context,
            {
                "authorized": True,
                "ambiguous": False,
                "context_patch": {
                    "ops": [{"path": "/context/package/inference/estimand", "value": text}],
                    "rationale": "Recorded researcher answer",
                },
            },
        )
    )
    apply_decision(store, run, decision.id, context=context, patch=None)
    d = supported_design()
    d.claim.estimand.value = text
    store.update_run(run, stage_id="S3")
    run_stage(
        run,
        "S3",
        Deps(
            store,
            ScriptedLLM(
                [
                    submit(
                        "submit_design",
                        {"design": plan_body(d), "summary": "Clarified draft"},
                    )
                ]
            ),
            ScriptedJudge([]),
            RunConfig(),
        ),
    )
    issue = store.issues(run)[0]

    def verify(messages, tools):
        focus = json.loads(messages[1]["content"].rsplit("\nVerification focus: ", 1)[1])
        response = focus["researcher_clarifications"][0]["response"]
        comparison = focus["comparisons"][0]
        assert response["text"] == text and response["researcher"] == "PI"
        assert comparison["parent_version"] is None and comparison["addressed_version"] == 1
        return submit(
            "submit_verdicts",
            {
                "verdicts": [
                    {
                        "issue_id": issue.id,
                        "verdict": "resolved",
                        "grounds": "Draft implements recorded ITT; no parent exists.",
                        "evidence_refs": ["researcher:estimand:intent-answer"],
                    }
                ],
                "regressions": [],
            },
        )

    deps = Deps(
        store,
        ScriptedLLM([verify]),
        ScriptedJudge(
            [
                {"next_action": "VERIFY"},
                {issue.id: "resolved"},
                {"next_action": "STOP"},
            ]
        ),
        RunConfig(),
    )
    run_stage(run, "S4", deps)
    assert store.issues(run)[0].status == "resolved"
    assert store.decision(run, decision.id).verification["status"] == "verified"
    store.close()


@pytest.mark.parametrize("legacy", [False, True, "missing", "invalid"])
def test_each_researcher_decision_keeps_its_comparison_and_verification(tmp_path, legacy):
    import json

    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import apply_decision, interpret, respond
    from experiment_design.workflow import run_stage

    store = Store(tmp_path / "independent-decisions.db")
    run = start_run(
        source_snapshot(), Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig()), {}
    )
    store.save_context(run, source_context())
    store.add_version(run, supported_design(), author="design", parent=None)
    for name, patch in (("one", {"ops": [], "rationale": "Confirm current intent"}), ("two", None)):
        store.save_decision(
            ResearcherDecision(
                id=name,
                run_id=run,
                binding=store.binding(run),
                status="AWAITING_RESEARCHER",
                problem="Confirm current intent",
                affected_paths=["/claim/estimand"],
            )
        )
        answered = respond(
            store,
            run,
            name,
            DecisionResponse(
                revision=1,
                action="PROPOSE_ALTERNATIVE",
                text="Keep current estimand",
                researcher="PI",
                response_id=name + "-reply",
            ),
        )
        interpreted = interpret(answered, store.context(run), {"authorized": True, "patch": patch})
        store.save_decision(interpreted)
        from experiment_design.contracts import Patch

        applied = apply_decision(
            store,
            run,
            name,
            context=store.context(run),
            patch=Patch.model_validate(patch) if patch is not None else None,
        )
        issue = next(i for i in store.issues(run) if i.id == applied.application["issue_id"])
        issue.status = "rebutted"
        issue.rebuttal = "Already implements researcher intent"
        if legacy:
            issue.addressed_in = None
        if legacy in {"missing", "invalid"}:
            applied.application["version"] = None if legacy == "missing" else 999
            store.save_decision(applied)
        store.save_issue(issue)
    store.add_version(run, supported_design(), author="design", parent=2)
    store.update_run(run, stage_id="S4")
    issues = store.issues(run)

    def verify(messages, tools):
        focus = json.loads(messages[1]["content"].rsplit("\nVerification focus: ", 1)[1])
        expected = (
            [(None, None), (None, None)] if legacy in {"missing", "invalid"} else [(2, 1), (2, 1)]
        )
        assert [
            (c["addressed_version"], c["parent_version"]) for c in focus["comparisons"]
        ] == expected
        assert all("reconciliation" in d for d in focus["researcher_clarifications"])
        return submit(
            "submit_verdicts",
            {
                "verdicts": [
                    {
                        "issue_id": issue.id,
                        "verdict": verdict,
                        "grounds": "Independent comparison",
                        "evidence_refs": ["researcher:" + name + ":" + name + "-reply"],
                    }
                    for issue, name, verdict in zip(
                        issues, ("one", "two"), ("resolved", "uncertain"), strict=True
                    )
                ],
                "regressions": [],
            },
        )

    run_stage(
        run,
        "S4",
        Deps(
            store,
            ScriptedLLM([verify]),
            ScriptedJudge(
                [
                    {"next_action": "VERIFY"},
                    {issues[0].id: "resolved", issues[1].id: "unresolved"},
                    {"next_action": "STOP"},
                ]
            ),
            RunConfig(),
        ),
    )
    assert store.decision(run, "one").verification == {
        "status": "verified",
        "evidence_refs": ["researcher:one:one-reply"],
    }
    assert store.decision(run, "two").verification["status"] != "verified"
    assert store.issues(run)[1].status == "unresolved"
    store.close()


def test_researcher_reconciliation_flows_through_scientific_review_to_publication(tmp_path):
    import json

    from experiment_design.contracts import Assumption, DecisionResponse
    from experiment_design.decisions import respond
    from experiment_design.output import export

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


def test_partial_decision_answers_resume_only_independent_work(tmp_path):
    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import respond
    from tests.support.scenarios import decision_state

    store, run, context, decision = decision_state(tmp_path)
    store.update_run(run, stage_id="S4", status="NEEDS_RESEARCHER")
    waiting = store.save_decision(
        ResearcherDecision(
            id="other",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Confirm inclusion",
            affected_paths=["/sampling/inclusion"],
        )
    )
    respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            text="Margin two points",
            researcher="PI",
            response_id="margin-answer",
        ),
    )
    interpretation = submit(
        "submit_interpretation",
        {
            "authorized": True,
            "ambiguous": False,
            "explanation": "Only margin was answered",
            "patch": {
                "ops": [{"path": "/claim/margin", "value": 2}],
                "rationale": "Researcher response",
            },
        },
    )
    deps = Deps(store, ScriptedLLM([interpretation]), ScriptedJudge([]), RunConfig())
    assert advance(run, deps) == "NEEDS_RESEARCHER"
    assert store.version(run).design.claim.margin.value == 2
    assert store.decision(run, decision.id).status == "APPLIED"
    assert store.decision(run, waiting.id).status == "AWAITING_RESEARCHER"
    assert len(deps.llm.calls) == 1
    assert store.run(run).stage_id == "S2"
    store.close()


def test_two_independent_recorded_answers_apply_without_stale_failure(tmp_path):
    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import respond
    from tests.support.scenarios import decision_state

    store, run, context, first = decision_state(tmp_path)
    store.update_run(run, stage_id="S4", status="NEEDS_RESEARCHER")
    second = store.save_decision(
        ResearcherDecision(
            id="inclusion",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Confirm inclusion",
            affected_paths=["/sampling/inclusion"],
        )
    )
    for decision, text in ((first, "Margin two points"), (second, "Include all classes")):
        respond(
            store,
            run,
            decision.id,
            DecisionResponse(
                revision=1,
                action="PROPOSE_ALTERNATIVE",
                text=text,
                researcher="PI",
                response_id=decision.id + "-reply",
            ),
        )
    store.save_decision(
        ResearcherDecision(
            id="remaining",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Confirm resources",
            affected_paths=["/protocol/resources"],
        )
    )
    responses = [
        submit(
            "submit_interpretation",
            {
                "authorized": True,
                "ambiguous": False,
                "explanation": "Researcher answer",
                "patch": {
                    "ops": [{"path": path, "value": value}],
                    "rationale": "Researcher response",
                },
            },
        )
        for path, value in (("/claim/margin", 2), ("/sampling/inclusion", "All classes"))
    ]
    deps = Deps(store, ScriptedLLM(responses), ScriptedJudge([]), RunConfig())
    assert advance(run, deps) == "NEEDS_RESEARCHER"
    other = store.decision(run, second.id)
    assert other.status == "APPLIED"
    assert other.revision == 2
    assert other.responses[-1].text == "Include all classes"
    assert store.decision(run, first.id).status == "APPLIED"
    assert store.version(run).design.sampling.inclusion == "All classes"
    assert len(deps.llm.calls) == 2
    store.close()


def test_overlapping_recorded_answer_is_represented_with_raw_reply(tmp_path):
    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import respond
    from tests.support.scenarios import decision_state

    store, run, context, first = decision_state(tmp_path)
    store.update_run(run, stage_id="S4")
    second = store.save_decision(
        ResearcherDecision(
            id="also-margin",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Which margin?",
            affected_paths=["/claim/margin"],
        )
    )
    for decision, text in ((first, "Two points"), (second, "Keep three points")):
        respond(
            store,
            run,
            decision.id,
            DecisionResponse(
                revision=1,
                action="PROPOSE_ALTERNATIVE",
                text=text,
                researcher="PI",
                response_id=decision.id,
            ),
        )
    deps = Deps(
        store,
        ScriptedLLM(
            [
                submit(
                    "submit_interpretation",
                    {
                        "authorized": True,
                        "ambiguous": False,
                        "explanation": "Two points",
                        "patch": {
                            "ops": [{"path": "/claim/margin", "value": 2}],
                            "rationale": "PI",
                        },
                    },
                )
            ]
        ),
        ScriptedJudge([]),
        RunConfig(),
    )
    assert advance(run, deps) == "NEEDS_RESEARCHER"
    waiting = store.decision(run, second.id)
    assert waiting.status == "AWAITING_RESEARCHER" and waiting.revision == 2
    assert waiting.responses[-1].text == "Keep three points"
    assert len(deps.llm.calls) == 1
    assert (
        respond(
            store,
            run,
            waiting.id,
            DecisionResponse(
                revision=2,
                action="PROPOSE_ALTERNATIVE",
                text="Keep three points",
                researcher="PI",
                response_id="confirmed",
            ),
        ).status
        == "ANSWERED"
    )
    store.close()


def test_two_deterministic_stages_do_not_call_provider_or_judge(tmp_path):
    from experiment_design.workflow import run_stage

    store = Store(tmp_path / "deterministic.db")
    deps = Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig())
    run = start_run(source_snapshot(), deps, {})
    result = run_stage(run, "S1", deps)
    assert result.output_refs == ["source-1"]
    store.update_run(run, stage_id="S7")
    assert run_stage(run, "S7", deps).status == "NEEDS_INPUT"
    assert deps.llm.calls == [] and deps.judge.calls == []
    store.close()


def test_failed_understanding_keeps_source_and_failure_reason(tmp_path):
    store = Store(tmp_path / "failed.db")
    deps = Deps(
        store, ScriptedLLM([LLMError("provider unavailable")]), ScriptedJudge([]), RunConfig()
    )
    run = start_run(source_snapshot(), deps, {})
    with pytest.raises(LLMError, match="unavailable"):
        advance(run, deps)
    usage = store.usage(run)
    assert usage.llm_calls == 1
    assert usage.llm_usage_missing_calls == 1
    assert usage.llm_cache_usage_missing_calls == 1
    assert usage.llm_tokens == 0
    assert store.run(run).stage_id == "S2" and store.run(run).status == "FAILED"
    assert store.source_snapshot(run) == source_snapshot()
    assert any(
        e.type == "budget.stop" and e.data["reason"] == "provider_failure"
        for e in store.events(run)
    )
    store.close()


def test_qualification_feedback_redesigns_and_requalifies_in_one_run(tmp_path):
    import json

    store = Store(tmp_path / "feedback.db")
    draft = supported_design()
    draft.measurement.data_collection = None

    def verified(messages, tools):
        observation = json.loads(
            next(m["content"] for m in reversed(messages) if m["role"] == "tool")
        )
        return submit(
            "submit_verdicts",
            {
                "verdicts": [
                    {
                        "issue_id": "I1",
                        "verdict": "resolved",
                        "grounds": "The actual child patch specifies data collection",
                        "evidence_refs": [observation["evidence_ref"]],
                    }
                ],
                "regressions": [],
            },
        )

    responses = normal_responses()
    responses[1] = submit(
        "submit_design", {"design": plan_body(draft), "summary": "Incomplete draft"}
    )
    responses += [
        submit(
            "submit_revision",
            {
                "patch": {
                    "ops": [
                        {
                            "path": "/measurement/data_collection",
                            "value": "Collect end-of-term test scores using the specified rubric",
                        }
                    ],
                    "addresses": ["I1"],
                    "rationale": "Fill the qualification gap",
                }
            },
        ),
        *review_responses(),
        tool_call("read_diff", {"from_version": 1, "to_version": 2}),
        verified,
    ]
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge(
            [
                {"next_action": "REVIEW"},
                {"next_action": "QUALIFY"},
                {"next_action": "REDESIGN"},
                {"next_action": "REVIEW"},
                {"next_action": "VERIFY"},
                {"I1": "resolved"},
                {"next_action": "QUALIFY"},
            ]
        ),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    assert store.version(run).version == 2 and store.assessment(run).plan_complete
    assert store.issues(run)[0].status == "resolved"
    returns = [
        e
        for e in store.events(run)
        if e.type == "stage.result" and e.data["stage_id"] == "S5" and e.data["target"] == "S3"
    ]
    assert len(returns) == 1 and returns[0].data["originating_ref"] == "I1"
    assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    assert not any(e.agent_id == "handoff" for e in store.events(run))
    store.close()


def test_qualification_presents_required_identified_decision(tmp_path):
    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import respond
    from experiment_design.workflow import run_stage

    store = Store(tmp_path / "required.db")
    run = store.create_staged_run(source_snapshot(), {})
    store.save_context(run, source_context())
    store.add_version(run, supported_design(), author="design", parent=None)
    store.update_run(run, stage_id="S5")
    decision = store.save_decision(
        ResearcherDecision(
            id="needed",
            run_id=run,
            binding=store.binding(run),
            problem="Confirm interpretation",
            affected_paths=["/claim/estimand"],
        )
    )
    deps = Deps(store, ScriptedLLM(normal_responses()[4:]), ScriptedJudge([]), RunConfig())
    result = run_stage(run, "S5", deps)
    assert result.status == "NEEDS_INPUT"
    assert store.assessment(run).review_status == "NEEDS_INPUT"
    assert not store.assessment(run).plan_complete
    presented = store.decision(run, decision.id)
    assert presented.status == "AWAITING_RESEARCHER"
    reply = respond(
        store,
        run,
        presented.id,
        DecisionResponse(
            revision=presented.revision,
            action="PROPOSE_ALTERNATIVE",
            text="Use ITT class means",
            researcher="PI",
            response_id="needed-reply",
        ),
    )
    assert reply.status == "ANSWERED"
    store.close()


def test_resolved_scientific_issue_gets_fresh_qualification(tmp_path):
    from experiment_design.contracts import ReviewIssue
    from experiment_design.workflow import run_stage

    store = Store(tmp_path / "qualification.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    advance(run, deps)
    original = store.assessment(run)
    issue = store.save_issue(
        ReviewIssue(
            run_id=run,
            raised_by="researcher",
            version_raised=1,
            severity="MAJOR",
            paths=["/measurement/data_collection"],
            mechanism="Collection needs clarification",
            consequence="Scientific procedure is uncertain",
            basis="reasoning",
        )
    )
    assert store.publication_snapshot(run).assessment is None
    issue.status = "resolved"
    store.save_issue(issue)
    store.update_run(run, stage_id="S5")
    deps.llm = ScriptedLLM([])
    assert run_stage(run, "S5", deps).status == "COMPLETE"
    assert store.assessment(run).id != original.id and store.assessment(run).plan_complete
    assert deps.llm.calls == [] and store.issues(run)[0].status == "resolved"
    store.close()
