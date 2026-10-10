import pytest
from conftest import design

from experiment_design.contracts import ClusterSpec, Quantity
from experiment_design.science.power import compute_scenario, design_effect


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
