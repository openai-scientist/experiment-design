"""Calculator availability is evidence, not a verdict on scientific design validity."""

import json

import pytest

from experiment_design.agents.tools import ToolContext, build_tools
from experiment_design.config import RunConfig
from experiment_design.contracts import ArtifactBinding, CalculationRequest
from experiment_design.output import export
from experiment_design.patch import design_hash
from experiment_design.qualification import can_approve
from experiment_design.runtime.store import Store
from experiment_design.science.power import calculate
from experiment_design.science.validators import validate_design
from experiment_design.workflow import Deps, advance, start_run
from tests.support.builders import package
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit
from tests.support.scenarios import (
    plan_body,
    source_context,
    source_snapshot,
    supported_design,
)


@pytest.fixture
def calculation_request():
    return CalculationRequest(
        calculator_id=(supported_design()).analysis.method_id,
        binding=ArtifactBinding(source_snapshot_id="source", event_id=1),
        scenario_id="base",
        design=supported_design(),
    )


@pytest.mark.parametrize("method", ["clustered_noninferiority_mixed_model", "unimplemented_method"])
def test_unsupported_power_does_not_impose_pooled_t_assumptions(calculation_request, method):
    d = calculation_request.design
    calculation_request.calculator_id = method
    d.analysis.method_id = method
    d.analysis.model = "Mixed model with class random intercept"
    d.analysis.common_variance = None
    d.sampling.allocation = "1:1 class allocation; maximum 20 per arm"
    d.sampling.unit_of_analysis = "student"
    d.sampling.cluster.equal_size = False
    before = calculation_request.model_dump(mode="json")

    result = calculate(calculation_request)

    assert result.status == "not_computed"
    assert len(result.reasons) == 1
    assert method in result.reasons[0] and "not implemented" in result.reasons[0]
    assert "common variance" not in " ".join(result.reasons)
    assert "equal allocation" not in " ".join(result.reasons)
    assert result.implementation == "unavailable"
    assert any(
        "scientific validity" in note and "not assessed" in note for note in result.applicability
    )
    assert any("inputs" in note and "not assessed" in note for note in result.applicability)
    assert (
        result.power,
        result.required_n_per_arm,
        result.required_clusters_per_arm,
        result.df,
    ) == (None, None, None, None)
    assert result.binding == calculation_request.binding
    assert calculation_request.model_dump(mode="json") == before


def test_missing_method_is_a_specification_gap_not_an_unsupported_sap(calculation_request):
    calculation_request.calculator_id = None
    calculation_request.design.analysis.method_id = None
    result = calculate(calculation_request)
    assert result.status == "not_computed" and result.power is None
    assert len(result.reasons) == 1
    assert "calculator_id is missing" in result.reasons[0]
    assert "not implemented" not in result.reasons[0]


def test_known_method_model_mismatch_is_not_an_unknown_calculator(calculation_request):
    calculation_request.design.analysis.model = "Mixed model with class random intercept"
    result = calculate(calculation_request)
    assert result.status == "not_computed" and result.power is None
    assert len(result.reasons) == 1
    assert "mismatch" in result.reasons[0]
    assert "cluster_equal_size_t" in result.reasons[0]
    assert "Pooled t test of cluster means" in result.reasons[0]
    assert "Mixed model with class random intercept" in result.reasons[0]
    assert "unsupported" not in result.reasons[0]


def test_known_method_with_missing_model_reports_a_specification_gap(calculation_request):
    calculation_request.design.analysis.model = None
    result = calculate(calculation_request)
    assert result.status == "not_computed" and result.power is None
    assert len(result.reasons) == 1
    assert "model is missing" in result.reasons[0]
    assert "cluster_equal_size_t" in result.reasons[0]
    assert "mismatch" not in result.reasons[0]


@pytest.mark.parametrize("gap", ["sd", "variance", "unattested_sd"])
def test_supported_calculator_still_requires_inputs_and_assumptions(calculation_request, gap):
    d = calculation_request.design
    if gap == "sd":
        d.scenarios[0].sd = None
        expected = "sd missing"
    elif gap == "variance":
        d.analysis.common_variance = None
        expected = "method requires equal allocation and explicit common variance assumption"
    else:
        d.scenarios[0].sd.provenance = "computed"
        expected = "sd: invalid or unattested quantitative provenance"
    result = calculate(calculation_request)
    assert expected in result.reasons
    assert result.status == "not_computed" and result.power is None
    assert result.required_n_per_arm is None and result.required_clusters_per_arm is None


@pytest.mark.parametrize(
    ("defect", "code", "severity"),
    [
        ("metric", "unit_mismatch", "MAJOR"),
        ("provenance", "llm_quantity_as_input", "MAJOR"),
        ("sidedness", "alpha_sidedness", "MAJOR"),
    ],
)
def test_unavailable_power_does_not_hide_independent_scientific_defects(
    calculation_request, defect, code, severity
):
    d = calculation_request.design
    d.analysis.method_id = "clustered_noninferiority_mixed_model"
    d.analysis.model = "Mixed model with class random intercept"
    if defect == "metric":
        d.measurement.primary_metric.unit = "kg"
    elif defect == "provenance":
        d.scenarios[0].sd.provenance = "llm"
    else:
        d.analysis.sidedness = "two_sided"
    calculation_request.binding.design_hash = design_hash(d)
    result = calculate(calculation_request)
    findings = validate_design(d, package(), {"S1"}, calculations=[result])
    assert any(f.code == code and f.severity == severity for f in findings)
    assert any(f.code == "no_computable_scenario" and f.severity == "MINOR" for f in findings)
    assert result.status == "not_computed" and result.power is None


def test_unsupported_power_still_rejects_forged_source_override(tmp_path):
    store = Store(tmp_path / "source-guard.db")
    try:
        run = store.create_staged_run(source_snapshot(), {})
        context = store.save_context(run, source_context())
        draft = supported_design()
        draft.analysis.method_id = "clustered_noninferiority_mixed_model"
        draft.analysis.model = "Mixed model with class random intercept"
        version = store.add_version(run, draft, author="design", parent=None)
        tool = build_tools(ToolContext(store, run, context.package, version, set()))[
            "compute_power"
        ]
        with pytest.raises(ValueError, match="source span does not support"):
            tool.handler(
                {
                    "overrides": {
                        "sd": {
                            "value": 99,
                            "unit": "points",
                            "provenance": "source",
                            "source_ref": "S1",
                            "source_span": "Standard deviation 10 points",
                        }
                    }
                }
            )
        assert store.version(run).design.scenarios[0].sd.value == 10
    finally:
        store.close()


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
