import pytest
from conftest import design, package
from test_stages import staged

from experiment_design.agents.tools import build_tools, context_brief, validate_refs
from experiment_design.contracts import AgentResult, AgentTask, ResearchContext, TaskBudget
from experiment_design.review import publish_cohort, review_context, start_cohort
from experiment_design.runtime.store import Store, TaskRecord


def review_state(tmp_path):
    store = Store(tmp_path / "review.db")
    run, _ = staged(store)
    store.save_context(
        run,
        ResearchContext(id="context", revision=1, source_snapshot_id="source-1", package=package()),
    )
    store.add_version(run, design(), author="design", parent=None)
    cohort = start_cohort(store, run, store.binding(run))
    return store, run, cohort


def member(store, run, cohort, role):
    task = AgentTask(
        id=cohort.member_task_ids[role],
        stage_id="S4",
        cohort_id=cohort.id,
        binding=cohort.binding,
        objective="Review",
        package_ref="math",
        design_version=1,
        allowed_tools=["read_issues"],
        budget=TaskBudget(max_turns=2, max_tokens=1000),
        exit_conditions=["Submit"],
    )
    store.save_task(run, TaskRecord(task=task))
    return task


def test_independent_cohort_has_atomic_publication_barrier(tmp_path):
    store, run, cohort = review_state(tmp_path)
    method = member(store, run, cohort, "methodologist")
    finding = {
        "severity": "MAJOR",
        "paths": ["/analysis/uncertainty"],
        "mechanism": "Private class uncertainty concern",
        "consequence": "Wrong coverage",
        "basis": "reasoning",
        "evidence_refs": ["S1"],
    }
    store.record_submission(
        method.id,
        AgentResult(findings_or_changes={"findings": [finding]}, completion_status="completed"),
    )
    skeptic = member(store, run, cohort, "skeptic")
    ctx = review_context(store, run, skeptic)
    assert finding["mechanism"] not in context_brief(ctx)
    assert build_tools(ctx)["read_issues"].handler({}) == []
    assert validate_refs(["tool:private-method-call"], ctx)
    with pytest.raises(ValueError, match="submission"):
        publish_cohort(store, run, cohort.id)
    assert store.issues(run) == []
    store.record_submission(
        skeptic.id, AgentResult(findings_or_changes={"findings": []}, completion_status="completed")
    )
    published = publish_cohort(store, run, cohort.id)
    assert published[0].mechanism == finding["mechanism"]
    count = len(store.events(run))
    assert publish_cohort(store, run, cohort.id) == published
    assert len(store.events(run)) == count
    assert store.cohort(run, cohort.id).published_event_id is not None
    store.close()


def test_context_change_prevents_mixed_snapshot_publication(tmp_path):
    store, run, cohort = review_state(tmp_path)
    for role in ("methodologist", "skeptic"):
        task = member(store, run, cohort, role)
        store.record_submission(
            task.id,
            AgentResult(findings_or_changes={"findings": []}, completion_status="completed"),
        )
    context = store.context(run).model_copy(update={"revision": 2})
    store.save_context(run, context)
    with pytest.raises(ValueError, match="binding"):
        publish_cohort(store, run, cohort.id)
    assert store.cohort(run, cohort.id).published_event_id is None
    store.close()


def test_revision_parent_and_verification_guard(tmp_path):
    from experiment_design.contracts import Patch, PatchOp, ReviewIssue
    from experiment_design.review import commit_revision, verification_options

    store, run, cohort = review_state(tmp_path)
    issue = store.save_issue(
        ReviewIssue(
            run_id=run,
            raised_by="methodologist",
            version_raised=1,
            severity="MAJOR",
            paths=["/analysis/uncertainty"],
            mechanism="Wrong uncertainty",
            consequence="Wrong coverage",
            basis="reasoning",
        )
    )
    patch = Patch(
        ops=[PatchOp(path="/analysis/uncertainty", value="Cluster t confidence bound")],
        rationale="Correct uncertainty",
        addresses=[issue.id],
    )
    updated = commit_revision(store, run, parent=1, patch=patch, issue_ids=[issue.id])
    assert updated.parent == 1
    assert store.issues(run)[0].status == "addressed"
    assert "resolved" not in verification_options(issue, "uncertain", [], ["tool:verify"])
    assert "resolved" in verification_options(issue, "resolved", [], ["tool:verify"])
    before = store.events(run)
    with pytest.raises(ValueError, match="parent"):
        commit_revision(store, run, parent=1, patch=patch, issue_ids=[issue.id])
    assert store.events(run) == before
    store.close()
