import pytest

from experiment_design.agents.tools import build_tools, context_brief, validate_refs
from experiment_design.contracts import AgentResult, AgentTask, ResearchContext, TaskBudget
from experiment_design.review import publish_cohort, review_context, start_cohort
from experiment_design.runtime.store import Store, TaskRecord
from tests.support.builders import design, package
from tests.support.scenarios import staged


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
    store.save_task(run, store.task(skeptic.id).model_copy(update={"status": "completed"}))
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


def test_cohort_sources_exclude_later_researcher_response(tmp_path):
    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.decisions import respond

    store, run, cohort = review_state(tmp_path)
    task = member(store, run, cohort, "skeptic")
    decision = store.save_decision(
        ResearcherDecision(
            id="later",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="New data",
            affected_paths=["/scenarios/0/sd"],
        )
    )
    respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            text="Late measurement: standard deviation 12 points",
            researcher="PI",
            response_id="late-data",
        ),
    )
    ctx = review_context(store, run, task)
    hits = build_tools(ctx)["lookup_evidence"].handler({"query": "Late measurement"})
    assert not any(h["source_id"] == "researcher:later:late-data" for h in hits)
    assert validate_refs(["researcher:later:late-data"], ctx)
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


def test_uncertain_verification_can_be_revised_within_existing_limits(tmp_path):
    from experiment_design.config import RunConfig
    from experiment_design.contracts import ReviewIssue
    from experiment_design.review import review_actions

    store, run, _ = review_state(tmp_path)
    store.update_run(run, reviewed_version=1)
    store.save_issue(
        ReviewIssue(
            run_id=run,
            raised_by="verifier",
            version_raised=1,
            severity="MAJOR",
            status="unresolved",
            paths=["/analysis/uncertainty"],
            mechanism="Evidence remains inconclusive",
            consequence="Coverage uncertain",
            basis="reasoning",
        )
    )
    actions = review_actions(store, run, RunConfig(max_revisions=1))
    assert "REVISE" in actions
    assert "QUALIFY" not in actions
    store.update_run(run, revisions_done=1)
    assert "REVISE" not in review_actions(store, run, RunConfig(max_revisions=1))
    store.close()


def test_unresolved_material_issue_is_included_in_the_revision_batch(tmp_path):
    from experiment_design.config import RunConfig
    from experiment_design.contracts import ReviewIssue
    from experiment_design.workflow import Deps, run_stage, start_run
    from tests.support.providers import ScriptedJudge, ScriptedLLM, submit
    from tests.support.scenarios import source_context, source_snapshot, supported_design

    store = Store(tmp_path / "repair.db")
    deps = Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig())
    run = start_run(source_snapshot(), deps, {})
    store.save_context(run, source_context())
    store.add_version(run, supported_design(), author="design", parent=None)
    issue = store.save_issue(
        ReviewIssue(
            run_id=run,
            raised_by="verifier",
            version_raised=1,
            severity="MAJOR",
            status="unresolved",
            paths=["/protocol/steps"],
            mechanism="Missing protocol detail",
            consequence="Cannot implement",
            basis="reasoning",
        )
    )
    store.update_run(run, stage_id="S3")
    deps.llm = ScriptedLLM(
        [
            submit(
                "submit_revision",
                {
                    "patch": {
                        "ops": [
                            {
                                "path": "/protocol/steps",
                                "value": ["Prespecified independent class-mean analysis"],
                            }
                        ],
                        "rationale": "Repair independently uncertain protocol",
                        "addresses": [issue.id],
                    }
                },
            )
        ]
    )
    assert run_stage(run, "S3", deps).status == "COMPLETE"
    assert store.version(run).version == 2
    assert store.version(run).design.protocol.steps == [
        "Prespecified independent class-mean analysis"
    ]
    assert store.issues(run)[0].status == "addressed"
    assert store.issues(run)[0].addressed_in == 2
    store.close()


def test_cohort_keeps_bound_context_and_rejects_membership_rewrite(tmp_path):
    from experiment_design.agents.tools import build_tools
    from experiment_design.contracts import AgentTask, TaskBudget
    from experiment_design.review import review_context, start_cohort
    from tests.support.scenarios import decision_state

    store, run, context, _ = decision_state(tmp_path)
    cohort = start_cohort(store, run, store.binding(run))
    task = AgentTask(
        id=cohort.member_task_ids["methodologist"],
        binding=cohort.binding,
        cohort_id=cohort.id,
        objective="Review",
        package_ref=context.package.id,
        design_version=1,
        budget=TaskBudget(max_turns=5, max_tokens=1000),
        allowed_tools=[],
        exit_conditions=[],
    )
    ctx = review_context(store, run, task)
    changed = context.model_copy(deep=True)
    changed.revision = 2
    changed.package.research_question = "New question after review began"
    store.save_context(run, changed)
    observed = build_tools(ctx)["read_context"].handler({})
    assert observed["revision"] == 1
    assert observed["package"]["research_question"] == context.package.research_question
    before = store.events(run)
    with pytest.raises(ValueError, match="immutable"):
        store.save_cohort(cohort.model_copy(update={"binding": store.binding(run)}))
    assert store.events(run) == before
    store.close()


def test_rebuttal_evidence_reaches_verifier_without_borrowing_computation(tmp_path):
    import json

    from experiment_design.config import RunConfig
    from experiment_design.workflow import Deps, advance, start_run
    from tests.support.providers import ScriptedJudge, ScriptedLLM, submit, tool_call
    from tests.support.scenarios import normal_responses, source_snapshot

    store = Store(tmp_path / "rebuttal.db")
    captured = {}

    def defend(messages, tools):
        observation = json.loads(messages[-1]["content"])
        captured["ref"] = observation["evidence_ref"]
        assert observation["result"][0]["status"] == "computed"
        return submit(
            "submit_revision",
            {
                "patch": {"ops": [], "rationale": "Defend the declared assumption"},
                "rebuttals": [
                    {
                        "issue_id": "I1",
                        "argument": "The sensitivity is disclosed",
                        "evidence_refs": [captured["ref"], "S1"],
                    }
                ],
            },
        )

    def inspect_support(messages, tools):
        focus = json.loads(messages[1]["content"].split("\nVerification focus: ")[1])
        defense = focus["rebuttals"][0]
        assert defense["issue_id"] == "I1" and defense["argument"] == "The sensitivity is disclosed"
        assert defense["evidence_refs"] == [captured["ref"], "S1"]
        evidence = defense["observations"][0]
        assert evidence["accepted"] and evidence["evidence_ref"] == captured["ref"]
        assert evidence["task_id"] == defense["task_id"]
        assert evidence["binding"] == defense["binding"]
        assert evidence["arguments"]["overrides"]["icc"]["value"] == 0.15
        assert evidence["result"]["value"][0]["counterfactual"] is True
        assert defense["sources"][0]["id"] == "S1"
        captured["defense_task"] = defense["task_id"]
        # Another task's computation is visible for critique, never an own-task attestation.
        return submit(
            "submit_verdicts",
            {
                "verdicts": [
                    {
                        "issue_id": "I1",
                        "verdict": "resolved",
                        "grounds": "Review sensitivity",
                        "evidence_refs": [captured["ref"]],
                    }
                ],
                "regressions": [],
            },
        )

    def fresh_read(messages, tools):
        assert "Unknown evidence ref" in messages[-1]["content"]
        return tool_call("read_design", {}, call_id="independent-inspection")

    def verdict(messages, tools):
        captured["verifier_ref"] = json.loads(messages[-1]["content"])["evidence_ref"]
        assert captured["verifier_ref"] != captured["ref"]
        return submit(
            "submit_verdicts",
            {
                "verdicts": [
                    {
                        "issue_id": "I1",
                        "verdict": "resolved",
                        "grounds": "Independently inspected assumptions and disclosed sensitivity",
                        "evidence_refs": [captured["verifier_ref"]],
                    }
                ],
                "regressions": [],
            },
        )

    responses = normal_responses()
    responses[2] = submit(
        "submit_findings",
        {
            "summary": "Challenge assumption",
            "findings": [
                {
                    "severity": "MAJOR",
                    "paths": ["/sampling/cluster/icc"],
                    "mechanism": "Sensitivity to the assumed dependence requires explanation",
                    "consequence": "The result may depend on the assumed correlation",
                    "basis": "reasoning",
                    "evidence_refs": ["S1"],
                }
            ],
        },
    )
    responses += [
        tool_call(
            "compute_power",
            {
                "calculator_id": "cluster_equal_size_t",
                "overrides": {"icc": {"value": 0.15, "provenance": "assumption", "unit": ""}},
            },
        ),
        defend,
        inspect_support,
        fresh_read,
        verdict,
    ]
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge(
            [
                {"next_action": "REVIEW"},
                {"next_action": "REVISE"},
                {"next_action": "VERIFY"},
                {"I1": "resolved"},
                {"next_action": "QUALIFY"},
            ]
        ),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    assert "verifier_ref" in captured, "Verifier must reject borrowed evidence and obtain its own"
    verification = next(e for e in store.events(run) if e.type == "verification")
    assert verification.data["evidence_refs"] == [captured["verifier_ref"]]
    assert store.issues(run)[0].status == "resolved"
    assert store.assessment(run).plan_complete is True
    assert store.task(captured["defense_task"]).task.stage_id == "S4"
    assert store.version(run).version == 1 and store.approvals(run) == []
    store.close()
