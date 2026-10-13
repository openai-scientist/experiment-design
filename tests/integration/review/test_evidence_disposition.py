"""Real review contracts; scripted responses are not evidence of model reasoning."""

import json

import pytest

from experiment_design.agents import methodologist, skeptic
from experiment_design.agents.tools import ToolContext, build_tools
from experiment_design.contracts import AgentTask, TaskBudget
from experiment_design.persistence.store import Store
from experiment_design.runtime.loop import run_agent
from experiment_design.science.validators import validate_design
from tests.support.providers import ScriptedLLM, submit, tool_call
from tests.support.scenarios import source_context, source_snapshot, supported_design


@pytest.mark.parametrize("reviewer", [methodologist, skeptic], ids=["methodologist", "skeptic"])
@pytest.mark.parametrize("case", ["computed", "unsupported", "missing_sd", "scientific_defect"])
def test_review_acceptance_does_not_conflate_numerical_status_or_choose_verdict(
    tmp_path, reviewer, case
):
    store = Store(tmp_path / "review.db")
    try:
        run = store.create_staged_run(source_snapshot(), {})
        context = store.save_context(run, source_context())
        design = supported_design()
        if case in {"unsupported", "scientific_defect"}:
            design.analysis.method_id = "clustered_noninferiority_mixed_model"
            design.analysis.model = "Student-level mixed model with class random intercept"
        if case == "missing_sd":
            design.scenarios[0].sd = None
        if case == "scientific_defect":
            design.analysis.sidedness = "two_sided"
            assert any(
                f.code == "alpha_sidedness" and f.severity == "MAJOR"
                for f in validate_design(design, context.package, {"S1"})
            )
        version = store.add_version(run, design, author="design", parent=None)
        task_id = "independent-" + reviewer.SPEC.agent_id
        ctx = ToolContext(store, run, context.package, version, set(), task_id=task_id)
        task = AgentTask(
            id=task_id,
            binding=ctx.binding,
            stage_id="S4",
            design_version=version.version,
            objective="Review the evidence without altering the design",
            package_ref=context.package.id,
            allowed_tools=list(reviewer.TOOLS),
            budget=TaskBudget(max_turns=2, max_tokens=1000),
            exit_conditions=["Submit independent review"],
        )
        ref = "tool:" + task_id + ":power"
        notes = {
            "computed": "Power is conditional on the supplied assumptions and bound design",
            "unsupported": "Calculator capability is unavailable; SAP validity is unassessed",
            "missing_sd": "Missing SD prevents quantitative assessment",
            "scientific_defect": "Two-sided inference conflicts with the one-sided claim",
        }
        finding = {
            "severity": "MAJOR" if case == "scientific_defect" else "MINOR",
            "paths": ["/analysis/sidedness"] if case == "scientific_defect" else ["/scenarios"],
            "mechanism": notes[case],
            "consequence": "Interpretation must respect the stated evidence boundary",
            "basis": "reasoning" if case == "scientific_defect" else "computation",
            "evidence_refs": ["S1"] if case == "scientific_defect" else [ref],
        }
        payload = {"findings": [finding], "summary": notes[case]}
        if reviewer is skeptic:
            payload["scenarios_checked"] = [ref]

        def respond_to_observation(messages, tools):
            # Verify production feedback, not the scripted provider's reasoning.
            feedback = json.loads(messages[-1]["content"])
            assert feedback["evidence_ref"] == ref
            result = feedback["result"][0]
            assert result["status"] == ("computed" if case == "computed" else "not_computed")
            if case == "computed":
                assert 0 < result["power"] < 1
            else:
                assert result["power"] is None and result["reasons"]
            return submit("submit_findings", payload)

        result = run_agent(
            reviewer.SPEC,
            task,
            reviewer.brief(ctx),
            build_tools(ctx),
            reviewer.submit_tool(ctx),
            ScriptedLLM(
                [
                    tool_call(
                        "compute_power",
                        {
                            "calculator_id": "cluster_equal_size_t",
                        },
                        call_id="power",
                    ),
                    respond_to_observation,
                ]
            ),
            store,
            run,
            ctx.accepted_call_ids,
        )
        assert result.completion_status == "completed"
        assert result.findings_or_changes["findings"][0]["severity"] == finding["severity"]
        assert result.findings_or_changes["summary"] == notes[case]
        if reviewer is skeptic:
            assert result.findings_or_changes["scenarios_checked"] == [ref]
        assert store.task(task_id).submission == result
        observation = store.accepted_observations(task_id)[0]
        assert observation.accepted and observation.evidence_ref == ref
        assert observation.task_id == task_id and observation.binding == ctx.binding
        events = [e for e in store.events(run) if e.type == "tool.call"]
        assert all(e.data["ok"] for e in events)
        assert store.version(run).design_hash == version.design_hash
    finally:
        store.close()
