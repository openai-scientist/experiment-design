from conftest import design, package

from experiment_design.science.validators import validate_design


def findings(d):
    return validate_design(d, package(), {"S1"})


def codes(d):
    return [f.code for f in findings(d)]


def test_clean_design_has_no_findings():
    assert findings(design()) == []


def test_missing_required():
    d = design()
    d.analysis.uncertainty = None
    f = findings(d)
    assert [(x.code, x.severity, x.path) for x in f] == [
        ("missing_required", "MAJOR", "/analysis/uncertainty"),
    ]


def test_claim_changed():
    d = design()
    d.claim.margin.value = 4.0
    assert codes(d) == ["claim_changed"]


def test_claim_missing():
    d = design()
    d.claim.direction.value = None
    d.claim.direction.origin = "missing"
    assert codes(d) == ["claim_missing", "no_computable_scenario"]


def test_unit_mismatch():
    d = design()
    d.measurement.primary_metric.unit = "kg"
    assert codes(d) == ["unit_mismatch"]


def test_cluster_unit_ignored():
    d = design()
    d.sampling.cluster = None
    d.sampling.n_per_arm = 500
    assert [(f.code, f.severity) for f in findings(d)] == [("cluster_unit_ignored", "BLOCKER")]


def test_unsourced_quantity():
    d = design()
    d.scenarios[0].sd.source_ref = "S9"
    assert codes(d) == ["unsourced_quantity"]


def test_llm_quantity_as_input():
    d = design()
    d.scenarios[0].sd.provenance = "llm"
    assert codes(d) == ["llm_quantity_as_input", "no_computable_scenario"]


def test_alpha_sidedness():
    d = design()
    d.analysis.sidedness = "two_sided"
    assert codes(d) == ["alpha_sidedness"]


def test_no_computable_scenario():
    d = design()
    d.scenarios[0].sd = None
    f = findings(d)
    assert [(x.code, x.severity) for x in f] == [("no_computable_scenario", "MINOR")]
