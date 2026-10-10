import hashlib

import pytest
from conftest import design, package
from fakes import ScriptedJudge, ScriptedLLM, submit, tool_call

from experiment_design.config import RunConfig
from experiment_design.contracts import SourceArtifact, SourceSnapshot
from experiment_design.runtime.llm import LLMError
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, WorkflowError, advance, approve, start_run


def source_snapshot():
    from experiment_design.patch import package_claim

    text = "\n".join(str(v) for v in package_claim(package()).values())
    text += (
        "\nMargin 3 points. Standard deviation 10 points. "
        "Class size 25. ICC 0.10. At most 60 classes."
    )
    return SourceSnapshot(
        id="source-1",
        artifacts=[
            SourceArtifact(
                ref="S1",
                path="hypothesis.md",
                text=text,
                sha256=hashlib.sha256(text.encode()).hexdigest(),
            )
        ],
    )


def supported_design():
    d = design()
    d.scenarios[0].sd.source_span = "Standard deviation 10 points"
    d.sampling.cluster.icc.source_span = "ICC 0.10"
    d.analysis.method_id = "cluster_equal_size_t"
    d.analysis.model = "Pooled t test of cluster means"
    d.analysis.common_variance = True
    d.sampling.unit_of_analysis = "class"
    d.sampling.cluster.clusters_per_arm = 25
    d.sampling.cluster.equal_size = d.sampling.cluster.independent_clusters = True
    return d


def source_context():
    from experiment_design.contracts import FieldAnnotation, ResearchContext
    from experiment_design.patch import package_claim

    p = package()
    p.constraints = ["At most 60 classes"]
    return ResearchContext(
        id="context",
        revision=1,
        source_snapshot_id="source-1",
        package=p,
        field_annotations={
            key: FieldAnnotation(
                status="source-stated",
                source_ref="S1",
                source_span="Margin 3 points" if key == "margin" else str(value),
            )
            for key, value in package_claim(p).items()
            if value is not None
        },
    )


def understanding_response():
    return submit(
        "submit_understanding",
        {"context": source_context().model_dump(mode="json"), "decisions": []},
    )


def review_responses():
    from test_workflow import chain

    return [
        submit("submit_findings", {"chain": chain(), "findings": [], "summary": "Method"}),
        submit("submit_findings", {"findings": [], "scenarios_checked": [], "summary": "Skeptic"}),
    ]


def normal_responses():
    return [
        understanding_response(),
        submit(
            "submit_design",
            {"design": supported_design().model_dump(mode="json"), "rationale": "Draft"},
        ),
        *review_responses(),
        inspection_response(),
        submit(
            "submit_handoff",
            {"handoff_complete": True, "risks": [], "findings": [], "evidence_refs": []},
        ),
    ]


def inspection_response():
    response = tool_call("read_design", {})
    response.tool_calls.extend(tool_call("compute_power", {}).tool_calls)
    return response


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
    assert store.assessment(run).handoff_complete
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
    assert advance(run, deps) != "REVIEWABLE"
    assert not store.assessment(run).handoff_complete
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
    assert len(resumed.llm.calls) == 3
    started = [
        e for e in store.events(run) if e.type == "task.started" and e.agent_id == "methodologist"
    ]
    assert len(started) == 1
    store.close()


def test_legacy_resume_refused_without_mutation(tmp_path):
    store = Store(tmp_path / "legacy.db")
    run = store.create_run(package(), {})
    before = store.events(run)
    with pytest.raises(WorkflowError, match="schema"):
        advance(run, Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig()))
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


def test_understanding_stage_commit_failure_keeps_submission_and_rolls_back_context(
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
    assert store.context(run) is None
    assert store.usage(run).llm_calls == 1
    monkeypatch.setattr(store, "commit_stage", commit)
    resumed = Deps(
        store,
        ScriptedLLM(normal_responses()[1:]),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    assert advance(run, resumed) == "REVIEWABLE"
    assert len(resumed.llm.calls) == 5
    store.close()


def test_partial_decision_answers_resume_only_independent_work(tmp_path):
    from test_decisions import decision_state

    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import respond

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
    from test_decisions import decision_state

    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import respond

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
    from test_decisions import decision_state

    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import respond

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
    assert store.run(run).stage_id == "S2" and store.run(run).status == "FAILED"
    assert store.source_snapshot(run) == source_snapshot()
    assert any(
        e.type == "budget.stop" and e.data["reason"] == "provider_failure"
        for e in store.events(run)
    )
    store.close()


def test_qualification_returns_to_refinement_with_originating_issue(tmp_path):
    store = Store(tmp_path / "return.db")
    responses = normal_responses()[:5]
    responses.append(
        submit(
            "submit_handoff",
            {
                "handoff_complete": False,
                "risks": ["Data collection responsibility is unresolved"],
                "evidence_refs": [],
                "findings": [
                    {
                        "severity": "MAJOR",
                        "paths": ["/measurement/data_collection"],
                        "mechanism": "Data collection responsibility is unresolved",
                        "consequence": "Implementation needs another scientific choice",
                        "basis": "reasoning",
                        "evidence_refs": ["S1"],
                    }
                ],
            },
        )
    )
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge(
            [{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}, {"next_action": "STOP"}]
        ),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "INCONCLUSIVE"
    issue = store.issues(run)[0]
    event = next(
        e for e in store.events(run) if e.type == "stage.result" and e.data.get("stage_id") == "S5"
    )
    assert event.data["target"] == "S4" and event.data["originating_ref"] == issue.id
    assert store.run(run).stage_id == "S4" and not store.assessment(run).handoff_complete
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


def test_resolved_handoff_issue_gets_fresh_inspection(tmp_path):
    from experiment_design.workflow import run_stage

    store = Store(tmp_path / "inspection.db")
    run = store.create_staged_run(source_snapshot(), {})
    store.save_context(run, source_context())
    store.add_version(run, supported_design(), author="design", parent=None)
    store.update_run(run, stage_id="S5")
    finding = {
        "severity": "MAJOR",
        "paths": ["/measurement/data_collection"],
        "mechanism": "Data collection needs confirmation",
        "consequence": "Cannot execute",
        "basis": "reasoning",
        "evidence_refs": ["S1"],
    }
    first = submit(
        "submit_handoff",
        {"handoff_complete": False, "risks": [], "findings": [finding], "evidence_refs": []},
    )
    deps = Deps(
        store, ScriptedLLM([tool_call("compute_power", {}), first]), ScriptedJudge([]), RunConfig()
    )
    assert run_stage(run, "S5", deps).status == "REVISION_REQUIRED"
    issue = store.issues(run)[0]
    issue.status = "resolved"
    store.save_issue(issue)
    store.update_run(run, stage_id="S5")
    deps.llm = ScriptedLLM(normal_responses()[4:])
    assert run_stage(run, "S5", deps).status == "COMPLETE"
    assert len(deps.llm.calls) == 2
    assert len(store.issues(run)) == 1
    assert store.issues(run)[0].status == "resolved"
    store.close()
