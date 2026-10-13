from experiment_design.config import RunConfig
from experiment_design.contracts import DecisionResponse, ResearcherDecision
from experiment_design.design.decisions import respond
from experiment_design.persistence.store import Store
from experiment_design.workflow import Deps, advance, run_stage, start_run
from tests.support.providers import ScriptedJudge, ScriptedLLM
from tests.support.scenarios import (
    normal_responses,
    plan_body,
    review_responses,
    source_context,
    source_snapshot,
    supported_design,
)


def test_qualification_presents_required_identified_decision(tmp_path):
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


def test_unreviewed_qualification_returns_to_critique_without_provider_work(tmp_path):
    from experiment_design.contracts import Design

    store = Store(tmp_path / "inspection.db")
    run = store.create_staged_run(source_snapshot(), {})
    store.save_context(run, source_context())
    store.add_version(
        run, Design.model_validate(plan_body(supported_design())), author="design", parent=None
    )
    store.update_run(run, stage_id="S5")
    deps = Deps(store, ScriptedLLM([]), ScriptedJudge([{"next_action": "CRITIQUE"}]), RunConfig())
    result = run_stage(run, "S5", deps)
    assert result.status == "REVISION_REQUIRED" and store.run(run).stage_id == "S4"
    assert result.issue_refs and deps.llm.calls == []
    assert store.assessment(run).plan_complete is False
    assert store.assessment(run).review_status == "NOT_READY"
    deps.llm = ScriptedLLM(review_responses())
    deps.judge = ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}])
    result = run_stage(run, "S4", deps)
    store.commit_stage(run, result, "S5")
    assert run_stage(run, "S5", deps).status == "COMPLETE"
    assert store.assessment(run).plan_complete is True
    assert len(deps.llm.calls) == 2
    store.close()
