"""Stored scientific choices must be usable without reverse engineering JSON."""

import json
import os
import shlex
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_stage_pipeline import source_context, source_snapshot, supported_design

from experiment_design.cli import _parser, _status
from experiment_design.contracts import (
    AgentResult,
    AgentTask,
    Assumption,
    DecisionOption,
    DecisionResponse,
    DesignChoice,
    Patch,
    Quantity,
    ReadinessAssessment,
    ResearcherDecision,
    TaskBudget,
)
from experiment_design.decisions import apply_decision, interpret, respond
from experiment_design.output import render_markdown, render_stage_artifacts
from experiment_design.runtime.store import Store, TaskRecord


def study(tmp_path):
    store = Store(tmp_path / "presentation.db")
    rid = store.create_staged_run(source_snapshot(), {})
    store.save_context(rid, source_context())
    store.add_version(rid, supported_design(), author="design", parent=None)
    return store, rid


def test_published_randomization_scope_accepts_only_its_canonical_field(tmp_path):
    store, rid = study(tmp_path)
    try:
        before = store.version(rid)
        context = store.context(rid)
        decision = store.save_decision(
            ResearcherDecision(
                id="randomization-choice",
                run_id=rid,
                binding=store.binding(rid),
                status="AWAITING_RESEARCHER",
                problem="Confirm allocation procedure",
                affected_paths=["/design/randomization"],
            )
        )
        response = DecisionResponse(
            revision=decision.revision,
            action="PROPOSE_ALTERNATIVE",
            text="Stratify classes by school and allocate 1:1",
            researcher="Fixture researcher",
            response_id="fixture-allocation",
        )
        answered = respond(store, rid, decision.id, response)
        interpretation = {
            "authorized": True,
            "ambiguous": False,
            "patch": {
                "ops": [{"path": "/sampling/randomization", "value": response.text}],
                "rationale": "Recorded fixture allocation instruction",
            },
        }
        unrelated = {
            **interpretation,
            "patch": {
                "ops": [{"path": "/claim/margin", "value": 2}],
                "rationale": "Outside scope",
            },
        }
        with pytest.raises(ValueError, match="scope"):
            interpret(answered, context, unrelated)
        interpreted = interpret(answered, context, interpretation)
        store.save_decision(interpreted)
        applied = apply_decision(
            store,
            rid,
            decision.id,
            context=context,
            patch=Patch.model_validate(interpreted.interpretation["patch"]),
        )
        assert store.version(rid).design.sampling.randomization == response.text
        assert store.version(rid).design.claim == before.design.claim
        assert applied.affected_paths == ["/design/randomization"]
        assert applied.responses[-1] == response
        assert applied.application["response_id"] == response.response_id
        assert applied.verification["status"] == "pending"
    finally:
        store.close()


def choice(store, rid):
    return ResearcherDecision(
        id="estimand-choice",
        run_id=rid,
        revision=3,
        binding=store.binding(rid),
        status="AWAITING_RESEARCHER",
        problem="Confirm the target effect",
        affected_paths=["/claim/estimand", "/analysis/model"],
        dependencies=["Population and assignment interpretation"],
        recommendation="Consider assigned treatment; confirmation still needed",
        options=[
            DecisionOption(
                id="itt",
                label="Assigned treatment effect",
                proposal={"claim.estimand": "Effect in randomized students"},
                consequences=["Retains randomized comparison"],
                uncertainty="Adherence does not define the target population",
                evidence_refs=["S1"],
            )
        ],
    )


def test_bound_choices_and_exact_response_provenance_in_both_markdown_outputs(tmp_path):
    store, rid = study(tmp_path)
    try:
        decision = choice(store, rid)
        decision.responses = [
            DecisionResponse(
                revision=2,
                action="PROPOSE_ALTERNATIVE",
                text="Previous exact response, pending reconciliation",
                researcher="Researcher Linh",
                response_id="reply-2",
                at=datetime(2026, 10, 11, 2, 3, tzinfo=UTC),
            )
        ]
        store.save_decision(decision)
        snapshot = store.publication_snapshot(rid)
        texts = [
            render_markdown(snapshot),
            render_stage_artifacts(store.stage_artifacts(rid, snapshot)),
        ]
        for text in texts:
            for value in (
                "estimand-choice",
                "revision 3",
                "AWAITING_RESEARCHER",
                "/claim/estimand",
                "/analysis/model",
                "Population and assignment interpretation",
                "Consider assigned treatment; confirmation still needed",
                "itt",
                "Assigned treatment effect",
                "Effect in randomized students",
                "Retains randomized comparison",
                "Adherence does not define the target population",
                "S1",
                "Previous exact response, pending reconciliation",
                "Researcher Linh",
                "revision 2",
                "2026-10-11T02:03:00+00:00",
                "reply-2",
            ):
                assert value in text
            assert "Recommendation (provisional)" in text
            assert "context revision 1" in text.lower()
            assert "checkpoint" in text.lower()
        assert snapshot.approval is None
        assert store.decision(rid, decision.id) == decision
    finally:
        store.close()


def test_cli_presents_option_consequences_and_exact_select_command(tmp_path, capsys):
    store, rid = study(tmp_path)
    try:
        decision = store.save_decision(choice(store, rid))
        _status(store, rid, Path("study.yaml"))
        text = capsys.readouterr().out
        assert "Retains randomized comparison" in text
        assert "/analysis/model" in text
        assert "--action SELECT_OPTION --option" in text
        assert f"decide {rid} {decision.id} --revision 3" in text
        assert "--action PROPOSE_ALTERNATIVE" in text
        assert "Recommendation (provisional)" in text
        assert not store.decision(rid, decision.id).responses
    finally:
        store.close()


@pytest.mark.parametrize(
    "option_id", ["itt", "student's", "student $(ignored)", "@choice", "--itt"]
)
def test_option_hint_round_trips_literal_id_in_operator_shell(tmp_path, capsys, option_id):
    store, rid = study(tmp_path)
    try:
        decision = choice(store, rid)
        decision.options[0].id = option_id
        store.save_decision(decision)
        _status(store, rid, Path("study.yaml"))
        command = next(
            line.strip()
            for line in capsys.readouterr().out.splitlines()
            if "--action SELECT_OPTION" in line
        )
        if os.name != "nt":
            args = shlex.split(command)
            assert _parser().parse_args(args[1:]).option == option_id
            return
        shell = shutil.which("powershell") or shutil.which("pwsh")
        assert shell is not None, "Repository requires PowerShell on Windows"
        script = r"""
$tokens = $null; $parseErrors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseInput(
    [Console]::In.ReadToEnd(), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { exit 1 }
$commands = $tree.FindAll({param($n)
    $n -is [System.Management.Automation.Language.CommandAst]}, $true)
$elements = $commands[0].CommandElements
$arguments = @($elements | ForEach-Object {
    if ($null -ne $_.Value) { [string]$_.Value } else { $_.Extent.Text }
})
ConvertTo-Json -InputObject $arguments -Compress
"""
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", script],
            input=command,
            text=True,
            capture_output=True,
            check=True,
        )
        args = json.loads(result.stdout)
        assert _parser().parse_args(args[1:]).option == option_id
    finally:
        store.close()


def test_scientific_stage_projection_preserves_rationale_and_critique_evidence(tmp_path):
    store, rid = study(tmp_path)
    try:
        d = supported_design()
        d.rationale = [
            DesignChoice(
                choice="Class assignment",
                why="Avoid within-class contamination",
                alternatives_considered=["Individual assignment risks spillover"],
                tradeoffs="Fewer independent units",
            )
        ]
        d.assumptions = [
            Assumption(
                id="A-ICC",
                statement="ICC requires confirmation",
                quantity=Quantity(value=0.1, provenance="assumption", note="Planning value only"),
            )
        ]
        store.add_version(rid, d, author="design", parent=1)
        binding = store.binding(rid)
        task = AgentTask(
            id="review-task",
            stage_id="S4",
            binding=binding,
            objective="Review clustering",
            package_ref=rid,
            design_version=2,
            allowed_tools=[],
            budget=TaskBudget(max_turns=1, max_tokens=100),
            exit_conditions=["Review submitted"],
        )
        store.append_event(rid, "task.started", {}, task_id=task.id, agent_id="skeptic")
        store.save_task(
            rid,
            TaskRecord(
                task=task,
                submission=AgentResult(
                    completion_status="completed",
                    findings_or_changes={
                        "summary": "Test cluster uncertainty",
                        "scenarios_checked": ["tool:review-task:icc-sensitivity"],
                        "findings": [
                            {
                                "severity": "MAJOR",
                                "paths": ["/sampling/cluster"],
                                "mechanism": "Unequal sizes may increase variance",
                                "consequence": "Nominal precision may be optimistic",
                                "basis": "unknown_assumption",
                                "evidence_refs": ["S1"],
                                "suggested_fix": "Verify the class-size distribution",
                            }
                        ],
                    },
                ),
            ),
        )
        store.save_assessment(
            ReadinessAssessment(
                id="assessment",
                run_id=rid,
                binding=store.binding(rid),
                review_status="NEEDS_INPUT",
                handoff_complete=False,
                blockers=["Confirm instrument scoring before downstream implementation"],
                handoff_risks=["Confirm instrument scoring before downstream implementation"],
                evidence_refs=["tool:handoff-task:design-inspection"],
            )
        )
        snapshot = store.publication_snapshot(rid)
        before = snapshot.model_dump(mode="json")
        edp = render_markdown(snapshot)
        stages = render_stage_artifacts(store.stage_artifacts(rid, snapshot))
        assert "Individual assignment risks spillover" in edp
        assert "Planning value only" in edp
        s2 = stages.split("## S2")[1].split("## S3")[0]
        assert "Lower bound exceeds -3 points" in s2
        assert "source-stated" in s2 and "S1" in s2
        assert "At most 60 classes" in s2
        assert "review-task" in stages and "checkpoint" in stages
        assert "/sampling/cluster" in stages and "unknown_assumption" in stages
        assert "Verify the class-size distribution" in stages
        assert "tool:review-task:icc-sensitivity" in stages
        for text in (edp, stages):
            assert text.count("Confirm instrument scoring before downstream implementation") == 1
            assert "tool:handoff-task:design-inspection" in text
        assert snapshot.model_dump(mode="json") == before
        assert store.publication_snapshot(rid).model_dump(mode="json") == before
    finally:
        store.close()
