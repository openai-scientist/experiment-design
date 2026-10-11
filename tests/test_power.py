import pytest
from conftest import design

from experiment_design.contracts import ClusterSpec, Quantity
from experiment_design.science.power import compute_scenario, design_effect


@pytest.mark.parametrize(
    "case", ["independent51", "cluster20", "cluster24", "reversed20", "superiority64"]
)
def test_method_power_matches_independent_r_reference(case):
    import json
    from pathlib import Path

    from experiment_design.contracts import ArtifactBinding, CalculationRequest
    from experiment_design.science.power import calculate

    ref = json.loads(Path("tests/fixtures/power_reference.json").read_text())["cases"][case]
    d = design()
    d.analysis.common_variance = True
    if "20" in case or case == "cluster24":
        d.analysis.method_id = "cluster_equal_size_t"
        d.analysis.model = "Pooled t test of cluster means"
        d.sampling.unit_of_analysis = "class"
        d.sampling.cluster.equal_size = True
        d.sampling.cluster.independent_clusters = True
        d.sampling.cluster.clusters_per_arm = ref["n"]
        if case == "reversed20":
            d.claim.direction.value = "decrease"
    else:
        d.sampling.cluster = None
        d.sampling.n_per_arm = ref["n"]
        d.sampling.unit_of_randomization = d.sampling.unit_of_analysis = "participant"
        d.analysis.method_id = "independent_pooled_t"
        d.analysis.model = "Pooled two-sample t test"
        d.analysis.alpha = ref["alpha"]
        d.scenarios[0].sd = q(ref["sd"])
        if case == "independent51":
            d.claim.margin.value = 0.05
        else:
            d.claim.contrast_type.value = "superiority"
            d.analysis.sidedness = "two_sided"
            d.scenarios[0].effect = q(0.5)
    r = calculate(
        CalculationRequest(
            binding=ArtifactBinding(source_snapshot_id="source", event_id=1),
            scenario_id="base",
            design=d,
        )
    )
    assert r.power == pytest.approx(ref["power"], abs=1e-8)
    assert r.df == 2 * ref["n"] - 2
    assert r.mde is None
    if case == "independent51":
        assert r.power == pytest.approx(0.8059, abs=5e-5)


def test_cluster_t_integer_search_and_unsupported_method():
    from experiment_design.contracts import ArtifactBinding, CalculationRequest
    from experiment_design.science.power import calculate

    d = design()
    d.analysis.method_id = "cluster_equal_size_t"
    d.analysis.model = "Pooled t test of cluster means"
    d.analysis.common_variance = True
    d.sampling.unit_of_analysis = "class"
    d.sampling.cluster.equal_size = d.sampling.cluster.independent_clusters = True
    request = CalculationRequest(
        binding=ArtifactBinding(source_snapshot_id="source", event_id=1),
        scenario_id="base",
        design=d,
    )
    result = calculate(request)
    assert result.required_clusters_per_arm == 25
    for k, expected in [(24, False), (25, True)]:
        request.design.sampling.cluster.clusters_per_arm = k
        assert (calculate(request).power >= 0.8) == expected
    request.design.analysis.model = "Mixed model with class random intercept"
    unsupported = calculate(request)
    assert unsupported.status == "not_computed"
    assert "method" in " ".join(unsupported.reasons)
    request.design.analysis.model = "Pooled t test of cluster means"
    request.design.sampling.cluster.equal_size = False
    assert calculate(request).status == "not_computed"


def q(value, unit="points", provenance="assumption"):
    return Quantity(value=value, unit=unit, provenance=provenance)


def means(n=None):
    d = design()
    d.claim.contrast_type.value = "superiority"
    d.analysis.sidedness = "two_sided"
    d.analysis.alpha = 0.05
    d.sampling.cluster = None
    d.sampling.n_per_arm = n
    d.sampling.unit_of_analysis = d.sampling.unit_of_randomization
    d.scenarios[0].effect = q(0.5)
    d.scenarios[0].sd = q(1)
    return d


def calc(d):
    return compute_scenario(d, d.scenarios[0])


def test_superiority_means_sample_size():
    assert calc(means()).required_n_per_arm == 63


def test_superiority_means_power_at_63():
    assert calc(means(63)).power == pytest.approx(0.8013, abs=0.001)


def test_non_inferiority_power_uses_margin_as_null():
    d = means(63)
    d.claim.contrast_type.value = "non_inferiority"
    d.claim.margin.value = 0.5
    d.analysis.sidedness = "one_sided"
    d.analysis.alpha = 0.025
    d.scenarios[0].effect = q(0)
    assert calc(d).power == pytest.approx(0.8013, abs=0.001)


def test_proportions_sample_size():
    d = means()
    d.claim.outcome_kind.value = "binary"
    d.claim.outcome_unit.value = "proportion"
    d.scenarios[0].p_control = q(0.50, "proportion")
    d.scenarios[0].p_treatment = q(0.65, "proportion")
    assert calc(d).required_n_per_arm == 167


def clustered(count=None):
    d = means()
    d.sampling.cluster = ClusterSpec(
        unit="class",
        size=q(20, "students"),
        icc=q(0.05, ""),
        clusters_per_arm=count,
    )
    return d


def test_design_effect_and_clusters():
    assert design_effect(20, 0.05) == pytest.approx(1.95)
    r = calc(clustered())
    assert (r.required_clusters_per_arm, r.required_n_per_arm) == (7, 140)


def test_cluster_power():
    assert calc(clustered(8)).power == pytest.approx(0.8930, abs=0.001)


def test_mde_means():
    assert calc(means(63)).mde == pytest.approx(0.4992, abs=0.001)


def test_missing_sd_not_computed():
    d = means()
    d.scenarios[0].sd = None
    r = calc(d)
    assert r.status == "not_computed"
    assert "sd missing" in r.reasons
    assert r.required_n_per_arm is None


def test_missing_icc_cluster_not_computed():
    d = clustered(8)
    d.sampling.cluster.icc = None
    r = calc(d)
    assert r.status == "not_computed"
    assert "icc missing for cluster-randomized design" in r.reasons
    assert r.power is None


def test_llm_provenance_not_computed():
    d = means(63)
    d.scenarios[0].sd.provenance = "llm"
    r = calc(d)
    assert r.status == "not_computed"
    assert "sd is an LLM interpretation, not a valid input" in r.reasons


def test_percent_normalized_once():
    d = means()
    d.claim.outcome_kind.value = "binary"
    d.claim.outcome_unit.value = "proportion"
    d.scenarios[0].p_control = q(50, "%")
    d.scenarios[0].p_treatment = q(0.65, "proportion")
    assert calc(d).required_n_per_arm == 167
    assert d.scenarios[0].p_control.value == 50


def test_unit_mismatch_not_computed():
    d = means(63)
    d.scenarios[0].sd.unit = "kg"
    r = calc(d)
    assert r.status == "not_computed"
    assert any("unit" in reason for reason in r.reasons)


def test_overrides_do_not_mutate_design():
    d = clustered(8)
    before = d.model_dump_json()
    r = compute_scenario(d, d.scenarios[0], {"icc": q(0.2, "")})
    assert r.power < 0.8
    assert d.model_dump_json() == before


def test_zero_cluster_size_reports_invalid_input():
    d = clustered(20)
    d.sampling.cluster.size.value = 0
    d.sampling.cluster.icc.value = 1
    result = calc(d)
    assert result.status == "not_computed"
    assert "cluster size must be a positive whole head count" in result.reasons


def test_unattested_computed_input_not_computed():
    d = means(63)
    d.scenarios[0].sd = q(1, provenance="computed")
    result = calc(d)
    assert result.status == "not_computed"
    assert any("unattested computed quantity" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    "case",
    [
        "binary",
        "unequal_allocation",
        "variance",
        "dependent",
        "analysis_unit",
        "fractional_size",
        "sd_zero",
        "target",
        "one_cluster",
        "provenance",
        "outcome_unit",
    ],
)
def test_t_method_refuses_inputs_outside_its_applicability(case):
    from test_stage_pipeline import supported_design

    from experiment_design.contracts import ArtifactBinding, CalculationRequest
    from experiment_design.science.power import calculate

    d = supported_design()
    if case == "binary":
        d.claim.outcome_kind.value = "binary"
    elif case == "unequal_allocation":
        d.sampling.allocation = "2:1"
    elif case == "variance":
        d.analysis.common_variance = False
    elif case == "dependent":
        d.sampling.cluster.independent_clusters = False
    elif case == "analysis_unit":
        d.sampling.unit_of_analysis = "student"
    elif case == "fractional_size":
        d.sampling.cluster.size.value = 25.5
    elif case == "sd_zero":
        d.scenarios[0].sd.value = 0
    elif case == "target":
        d.scenarios[0].target_power = 1
    elif case == "one_cluster":
        d.sampling.cluster.clusters_per_arm = 1
    elif case == "provenance":
        d.scenarios[0].sd.provenance = "computed"
    elif case == "outcome_unit":
        d.scenarios[0].sd.unit = "kg"
    result = calculate(
        CalculationRequest(
            binding=ArtifactBinding(source_snapshot_id="source", event_id=1),
            scenario_id="base",
            design=d,
        )
    )
    assert result.status == "not_computed" and result.reasons
    assert result.power is None and result.required_n_per_arm is None


@pytest.mark.parametrize("unit", ["%", "kg", "people"])
def test_cluster_size_override_requires_canonical_count_unit(unit):
    from test_stage_pipeline import supported_design

    from experiment_design.contracts import ArtifactBinding, CalculationRequest
    from experiment_design.science.power import calculate

    result = calculate(
        CalculationRequest(
            binding=ArtifactBinding(source_snapshot_id="source", event_id=1),
            scenario_id="base",
            design=supported_design(),
            overrides={"size": q(25, unit)},
        )
    )
    assert result.status == "not_computed" and any("unit" in r for r in result.reasons)


@pytest.mark.parametrize("method", ["linear_mixed_model", "independent_pooled_t"])
def test_cluster_diagnostics_distinguish_unsupported_and_independent_methods(method):
    from test_stage_pipeline import supported_design

    from experiment_design.contracts import ArtifactBinding, CalculationRequest
    from experiment_design.science.power import calculate

    d = supported_design()
    d.analysis.method_id = method
    d.analysis.model = (
        "Mixed model with class random effect."
        if method == "linear_mixed_model"
        else "Pooled two-sample t test"
    )
    result = calculate(
        CalculationRequest(
            binding=ArtifactBinding(source_snapshot_id="source", event_id=1),
            scenario_id="base",
            design=d,
        )
    )
    assert result.status == "not_computed" and result.power is None
    independent_error = "independent method does not support cluster dependence"
    if method == "linear_mixed_model":
        assert any("not implemented" in reason and method in reason for reason in result.reasons)
        assert independent_error not in result.reasons
    else:
        assert independent_error in result.reasons
