"""Calculator availability is evidence, not a verdict on scientific design validity."""

import json

from experiment_design.config import RunConfig
from experiment_design.design.approval import can_approve
from experiment_design.persistence.store import Store
from experiment_design.publication import export
from experiment_design.workflow import Deps, advance, start_run
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit
from tests.support.scenarios import (
    plan_body,
    source_context,
    source_snapshot,
    supported_design,
)


def test_unsupported_computation_remains_a_disclosed_draft_limitation(tmp_path):
    draft = supported_design()
    draft.analysis.method_id = "clustered_noninferiority_mixed_model"
    draft.analysis.model = "Mixed model with class random intercept"
    draft.analysis.common_variance = None
    draft.sampling.unit_of_analysis = "student"
    context = source_context(method="Mixed model with class random intercept")
    context.unknowns = ["District SD and ICC remain unverified"]
    store = Store(tmp_path / "interpretation.db")
    deps = Deps(
        store,
        ScriptedLLM(
            [
                submit("submit_understanding", {"context": context.model_dump(mode="json")}),
                submit("submit_design", {"design": plan_body(draft), "summary": "Draft"}),
                submit(
                    "submit_findings",
                    {"findings": [], "summary": "Uncertainty retained"},
                ),
                submit(
                    "submit_findings",
                    {"findings": [], "scenarios_checked": [], "summary": "No new defect"},
                ),
            ]
        ),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    try:
        run = start_run(source_snapshot(method="Mixed model with class random intercept"), deps, {})
        assert advance(run, deps) == "REVIEWABLE"
        snapshot = store.publication_snapshot(run)
        assert snapshot.assessment.plan_complete
        assert not can_approve(snapshot)
        assert store.approvals(run) == []
        paths = export(store, run, tmp_path / "draft")
        data = json.loads(paths["exp_plan.json"].read_bytes())
        markdown = paths["exp_plan.md"].read_text(encoding="utf-8")
        assert "Draft" in markdown
        assert "common variance" not in markdown
        assert "District SD and ICC remain unverified" in markdown
        assert data["calculations"] == []
        assert data["design"]["analysis"]["model"] == "Mixed model with class random intercept"
        assert data["design"]["analysis"]["decision_rule"] == draft.analysis.decision_rule
        assert data["approval"] is None and not store.approvals(run)
        artifacts = store.stage_artifacts(run, snapshot)
        method = next(
            s for s in artifacts["stages"]["S4"]["submissions"] if s["agent_id"] == "methodologist"
        )
        assert method["result"]["findings_or_changes"]["summary"] == "Uncertainty retained"
        assert store.run(run).stage_id == "S7" and store.run(run).status == "REVIEWABLE"
    finally:
        store.close()
