"""Behavior contracts of the integrated scientific plan workflow."""

import hashlib

from experiment_design.config import RunConfig
from experiment_design.contracts import StageResult
from experiment_design.package import snapshot_sources
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, run_stage, start_run
from tests.support.exploration import exploration_payload
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit
from tests.support.scenarios import plan_body, source_context, supported_design


def test_ede_explores_alternatives_before_first_design(tmp_path):
    text = """Is AI feedback non-inferior?
AI feedback is non-inferior to teacher feedback
Lower bound exceeds -3 points
Lower bound is at most -3 points
Weekly AI feedback
Grade-7 classes in one district for one term
math score, points, continuous, increase
non_inferiority
ITT difference in class means
Margin 3 points. Standard deviation 10 points.
Class size 25. ICC 0.10. At most 60 classes.
"""
    path = tmp_path / "question.md"
    path.write_bytes(text.encode())
    source = snapshot_sources([path])
    context = source_context()
    context.package.hypothesis.method = None
    context.field_annotations.pop("/analysis/model")
    context.source_snapshot_id = source.id
    ref = source.artifacts[0].ref
    for annotation in context.field_annotations.values():
        annotation.source_ref = ref
    proposals = exploration_payload("conditional_tie", source_ref=ref)
    draft = supported_design()
    draft.scenarios[0].sd.source_ref = ref
    draft.sampling.cluster.icc.source_ref = ref
    store = Store(tmp_path / "ede.db")
    deps = Deps(
        store,
        ScriptedLLM(
            [
                submit("submit_understanding", {"context": context.model_dump(mode="json")}),
                submit("submit_exploration", proposals),
                submit(
                    "submit_design",
                    {
                        "design": plan_body(draft),
                        "summary": "Selected probe-b",
                    },
                ),
            ]
        ),
        ScriptedJudge([{"next_probe": "recommend:probe-b"}]),
        RunConfig(),
    )
    run = start_run(source, deps, {})
    store.commit_stage(
        run,
        StageResult(
            stage_id="S1", status="COMPLETE", binding=store.binding(run), reason="Sources captured"
        ),
        "S2",
    )
    result = run_stage(run, "S2", deps)
    assert result.status == "COMPLETE"
    frozen = store.exploration_snapshot(run)
    assert frozen is not None
    assert frozen.run_id == run and frozen.selection.choice == "recommend:probe-b"
    assert [c.id for c in frozen.submission.candidates] == ["probe-a", "probe-b"]
    assert store.task(frozen.task_id).task.stage_id == "S2"
    assert store.version(run) is None
    run_stage(run, "S3", deps)
    assert store.version(run).parent is None
    events = store.events(run)
    frozen_event = next(
        e.event_id
        for e in events
        if e.type == "artifact" and e.data["kind"] == "exploration_snapshot"
    )
    design_event = next(e.event_id for e in events if e.type == "design.version")
    assert frozen_event < design_event
    assert store.source_snapshot(run).artifacts[0].text == text
    assert (
        store.source_snapshot(run).artifacts[0].sha256 == hashlib.sha256(text.encode()).hexdigest()
    )
    assert store.decisions(run) == [] and store.approvals(run) == []
    assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    import json

    from experiment_design.output import export

    paths = export(store, run, tmp_path / "selected-plan")
    data = json.loads(paths["exp_plan.json"].read_bytes())
    assert data["selected_candidate"]["candidate_id"] == "probe-b"
    assert "probe-b" in deps.llm.calls[-1][0][1]["content"]


def test_rejected_direction_reexplores_and_redesigns(tmp_path):
    from experiment_design.contracts import Design, ReviewCohort
    from tests.support.scenarios import review_responses, source_snapshot

    store = Store(tmp_path / "redesign.db")
    context = source_context()
    context.package.hypothesis.method = None
    context.field_annotations.pop("/analysis/model")
    deps = Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig())
    run = start_run(source_snapshot(), deps, {})
    store.save_context(run, context)
    parent = store.add_version(
        run, Design.model_validate(plan_body(supported_design())), author="design", parent=None
    )
    store.update_run(run, stage_id="S3")
    store.commit_stage(
        run,
        StageResult(
            stage_id="S3", status="COMPLETE", binding=store.binding(run), reason="Initial design"
        ),
        "S4",
    )
    finding = {
        "severity": "MAJOR",
        "paths": ["/analysis/model"],
        "mechanism": "The direction ignores plausible confounding",
        "consequence": "Reconsider the observational strategy before refinement",
        "basis": "reasoning",
        "evidence_refs": [],
    }
    deps.llm = ScriptedLLM(
        [
            submit("submit_findings", {"findings": [finding], "summary": "Reject direction"}),
            review_responses()[1],
        ]
    )
    deps.judge = ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "REEXPLORE"}])
    result = run_stage(run, "S4", deps)
    assert result.status == "REVISION_REQUIRED" and store.run(run).stage_id == "S2"
    issue = store.issues(run)[0]
    assert result.issue_refs == [issue.id]
    used = store.usage(run).llm_tokens
    context.revision = 2
    deps.llm = ScriptedLLM(
        [
            submit("submit_understanding", {"context": context.model_dump(mode="json")}),
            submit("submit_exploration", exploration_payload("conditional_tie", source_ref="S1")),
        ]
    )
    deps.judge = ScriptedJudge([{"next_probe": "recommend:probe-b"}])
    run_stage(run, "S2", deps)
    frozen = store.exploration_snapshot(run)
    assert frozen.origin_issue_refs == [issue.id]
    patch = {
        "ops": [
            {
                "path": "/analysis/model",
                "value": "Adjusted longitudinal comparison with sensitivity analysis",
            }
        ],
        "rationale": "Selected an alternative addressing confounding",
        "addresses": [issue.id],
    }
    deps.llm = ScriptedLLM([submit("submit_revision", {"patch": patch})])
    run_stage(run, "S3", deps)
    changed = store.version(run)
    assert changed.parent == parent.version and changed.design_hash != parent.design_hash
    assert store.version(run, parent.version).design_hash == parent.design_hash
    assert store.usage(run).llm_tokens > used and store.run(run).revisions_done == 1
    deps.llm = ScriptedLLM(review_responses())
    deps.judge = ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}])
    run_stage(run, "S4", deps)
    cohorts = store._artifact_records(run, "cohort", ReviewCohort)
    assert len(cohorts) == 2
    assert (
        cohorts[1].binding.context_revision == 2
        and cohorts[1].binding.design_version == changed.version
    )
    assert set(cohorts[0].member_task_ids.values()).isdisjoint(cohorts[1].member_task_ids.values())
    assert store.approvals(run) == [] and store.decisions(run) == []


def test_exp_plan_export_is_offline_consistent_and_frozen(tmp_path):
    import json

    from experiment_design.output import export
    from experiment_design.workflow import advance
    from tests.support.providers import tool_call
    from tests.support.scenarios import normal_responses, source_snapshot

    def method_review(messages, tools):
        brief = json.loads(messages[1]["content"].split("\n")[-1])
        assert brief["design"]["protocol"]["steps"] == store.version(run).design.protocol.steps
        return tool_call("read_design", {}, call_id="inspection")

    def skeptic_review(messages, tools):
        brief = json.loads(messages[1]["content"].split("\n")[-1])
        assert brief["design"] == store.version(run).design.model_dump(mode="json")
        return submit(
            "submit_findings", {"findings": [], "summary": "Independent alternative check"}
        )

    def inspected_review(messages, tools):
        evidence_ref = json.loads(messages[-1]["content"])["evidence_ref"]
        return submit(
            "submit_findings",
            {
                "findings": [
                    {
                        "severity": "MINOR",
                        "paths": ["/protocol/steps"],
                        "mechanism": "Delivery logs should retain the specified sequence",
                        "consequence": "Supports reproducibility with the same scientific choices",
                        "basis": "reasoning",
                        "evidence_refs": [evidence_ref],
                    }
                ],
                "summary": "Independent protocol justification",
            },
        )

    responses = normal_responses()[:2] + [
        method_review,
        inspected_review,
        skeptic_review,
    ]
    context = source_context()
    constraints = ["Use only local anonymized records; no external service access"]
    budget = "Two engineer-days and four GPU-hours"
    context.package.constraints = constraints
    context.package.budget = budget
    responses[0] = submit("submit_understanding", {"context": context.model_dump(mode="json")})
    source = source_snapshot()
    source.artifacts[0].text += "\n" + constraints[0] + "\n" + budget
    source.artifacts[0].sha256 = hashlib.sha256(source.artifacts[0].text.encode()).hexdigest()

    store = Store(tmp_path / "publish.db")
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source, deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    paths = export(store, run, tmp_path / "plan")
    assert set(paths) == {"exp_plan.md", "exp_plan.json", "manifest.json"}
    data = json.loads(paths["exp_plan.json"].read_bytes())
    manifest = json.loads(paths["manifest.json"].read_bytes())
    assert data["binding"] == manifest["binding"]
    assert data["design"] == store.version(run).design.model_dump(mode="json")
    assert data["assessment"]["plan_complete"] is True
    submissions = data["review"]["submissions"]
    assert {s["agent_id"] for s in submissions} == {"methodologist", "skeptic"}
    assert {s["result"]["findings_or_changes"]["summary"] for s in submissions} == {
        "Independent protocol justification",
        "Independent alternative check",
    }
    assert data["review"]["cohorts"][0]["binding"]["design_hash"] == manifest["design_hash"]
    ref = data["issues"][0]["evidence_refs"][0]
    observation = next(o for o in data["observations"] if o["evidence_ref"] == ref)
    assert observation["tool_name"] == "read_design" and observation["accepted"] is True
    assert observation["binding"]["design_hash"] == manifest["design_hash"]
    assert observation["task_id"] in data["review"]["cohorts"][0]["member_task_ids"].values()
    assert (
        "execution_ready" not in data["assessment"] and "handoff_complete" not in data["assessment"]
    )
    text = paths["exp_plan.md"].read_text(encoding="utf-8")
    assert constraints == data["context"]["package"]["constraints"]
    assert budget == data["context"]["package"]["budget"]
    assert all(c in text for c in constraints) and budget in text
    assert (
        "Execution protocol" in text
        and "Expected outputs" in text
        and "Implementation boundaries" in text
    )
    assert not any(e.agent_id == "handoff" for e in store.events(run))
    before = {name: path.read_bytes() for name, path in paths.items()}
    context = store.context(run)
    context.revision += 1
    context.unknowns.append("New evidence requires a new assessment")
    context.package.constraints = ["Changed constraint must not overwrite the frozen plan"]
    context.package.budget = "Changed budget"
    store.save_context(run, context)
    historical = export(store, run, tmp_path / "frozen", version=1)
    assert {name: path.read_bytes() for name, path in historical.items()} == before
    assert store.publication_snapshot(run).assessment is None


def test_exp_plan_export_rejects_corrupt_or_different_destination(tmp_path):
    import pytest

    from experiment_design.output import export
    from experiment_design.workflow import advance
    from tests.support.scenarios import normal_responses, source_snapshot

    store = Store(tmp_path / "publish.db")
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    advance(run, deps)
    out = tmp_path / "plan"
    paths = export(store, run, out)
    original = {name: path.read_bytes() for name, path in paths.items()}
    assert export(store, run, out) == paths
    paths["exp_plan.md"].write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="destination"):
        export(store, run, out)
    paths["exp_plan.md"].write_bytes(original["exp_plan.md"])
    extra = out / "obsolete.json"
    extra.write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="destination"):
        export(store, run, out)
    assert extra.read_bytes() == b"unexpected"
