import json
from pathlib import Path

import pytest

from experiment_design.contracts import CalculationRequest, ClusterInputs, Quantity
from experiment_design.science.power import calculate


def q(value, unit="points", provenance="assumption"):
    return Quantity(value=value, unit=unit, provenance=provenance)


def request(cluster=False):
    return CalculationRequest(
        calculator_id="cluster_equal_size_t" if cluster else "independent_pooled_t",
        model="Pooled t test of cluster means" if cluster else "Pooled two-sample t test",
        contrast_type="non_inferiority",
        direction="increase",
        outcome_kind="continuous",
        outcome_unit="points",
        alpha=0.025,
        sidedness="one_sided",
        margin=q(3),
        effect=q(0),
        sd=q(10),
        n_per_arm=None if cluster else 51,
        target_power=0.8,
        common_variance=True,
        unit_of_randomization="class" if cluster else "participant",
        unit_of_analysis="class" if cluster else "participant",
        cluster=ClusterInputs(
            unit="class",
            size=q(25, "students"),
            icc=q(0.1, ""),
            clusters_per_arm=20,
            equal_size=True,
            independent_clusters=True,
        )
        if cluster
        else None,
    )


@pytest.mark.parametrize(
    "case", ["independent51", "cluster20", "cluster24", "reversed20", "superiority64"]
)
def test_preserved_independent_r_reference(case):
    ref = json.loads(Path("tests/fixtures/power_reference.json").read_text())["cases"][case]
    req = request(case in {"cluster20", "cluster24", "reversed20"})
    if req.cluster:
        req.cluster.clusters_per_arm = ref["n"]
    else:
        req.n_per_arm = ref["n"]
        req.sd = q(ref["sd"])
        req.alpha = ref["alpha"]
        if case == "independent51":
            req.margin = q(0.05)
        else:
            req.contrast_type = "superiority"
            req.effect = q(0.5)
            req.sidedness = "two_sided"
    if case == "reversed20":
        req.direction = "decrease"
    result = calculate(req)
    assert result.status == "computed", result.reasons
    assert result.power == pytest.approx(ref["power"], abs=1e-8)
    assert result.df == 2 * ref["n"] - 2
    assert result.mde is None


def test_cluster_integer_search_and_request_identity():
    req = request(True)
    before = req.model_dump_json()
    result = calculate(req)
    assert result.required_clusters_per_arm == 25
    assert result.required_n_per_arm == 625
    assert before == req.model_dump_json()
    assert calculate(req).id == result.id == "calc:" + result.request_hash
    req.cluster.clusters_per_arm = 24
    assert calculate(req).power < 0.8
    req.cluster.clusters_per_arm = 25
    assert calculate(req).power >= 0.8
    assert calculate(req).id != result.id


@pytest.mark.parametrize(
    "defect, expected",
    [
        ("model", "mismatch"),
        ("binary", "continuous"),
        ("variance", "common variance"),
        ("allocation", "equal allocation"),
        ("analysis_unit", "cluster level"),
        ("dependent", "independent clusters"),
        ("size", "whole count"),
        ("size_unit", "count of individuals"),
        ("icc", "outside"),
        ("sd", "positive"),
        ("unit", "does not match"),
        ("target", "target power"),
        ("count", "at least two"),
        ("provenance", "unattested"),
        ("margin_provenance", "unattested"),
        ("source_ref", "source reference"),
        ("missing", "sd missing"),
    ],
)
def test_applicability_and_authority_guards(defect, expected):
    req = request(True)
    if defect == "model":
        req.model = "Mixed model"
    elif defect == "binary":
        req.outcome_kind = "binary"
    elif defect == "variance":
        req.common_variance = False
    elif defect == "allocation":
        req.allocation = "2:1"
    elif defect == "analysis_unit":
        req.unit_of_analysis = "student"
    elif defect == "dependent":
        req.cluster.independent_clusters = False
    elif defect == "size":
        req.cluster.size.value = 25.5
    elif defect == "size_unit":
        req.cluster.size.unit = "kg"
    elif defect == "icc":
        req.cluster.icc.value = 2
    elif defect == "sd":
        req.sd.value = 0
    elif defect == "unit":
        req.sd.unit = "kg"
    elif defect == "target":
        req.target_power = 1
    elif defect == "count":
        req.cluster.clusters_per_arm = 1
    elif defect == "provenance":
        req.sd.provenance = "computed"
    elif defect == "margin_provenance":
        req.margin.provenance = "llm"
    elif defect == "source_ref":
        req.sd.provenance = "source"
    elif defect == "missing":
        req.sd = None
    result = calculate(req)
    assert result.status == "not_computed"
    assert any(expected in reason for reason in result.reasons), result.reasons
    assert result.power is None and result.required_n_per_arm is None


@pytest.mark.parametrize("calculator", [None, "mixed_model", "unknown"])
def test_unimplemented_calculator_does_not_reject_methodology(calculator):
    req = request(True)
    req.calculator_id = calculator
    req.model = "Mixed model with class random intercept"
    req.common_variance = None
    req.cluster.equal_size = False
    result = calculate(req)
    assert result.status == "not_computed" and len(result.reasons) == 1
    assert result.implementation == "unavailable"
    assert "common variance" not in result.reasons[0]
    assert any(
        "scientific validity" in note and "not assessed" in note for note in result.applicability
    )


def test_independent_units_must_be_declared():
    req = request()
    req.unit_of_analysis = req.unit_of_randomization = None
    assert calculate(req).status == "not_computed"
