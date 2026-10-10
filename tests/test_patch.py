import pytest
from conftest import design, package

from experiment_design.contracts import Patch, PatchOp
from experiment_design.patch import (
    PatchError,
    apply_patch,
    design_diff,
    design_hash,
    initial_design,
    set_claim,
)


def patch(path, value):
    return Patch(ops=[PatchOp(path=path, value=value)], rationale="Correct the design")


def test_initial_design_keeps_package_claim():
    d = design()
    d.claim.margin.value = 5
    with pytest.raises(PatchError, match="margin.*researcher-owned"):
        initial_design(package(), d)


def test_initial_design_labels_designer_default():
    p, d = package(), design()
    p.inference.estimand = None
    d.claim.estimand.value = "ITT mean difference"
    assert initial_design(p, d).claim.estimand.origin == "designer_default"


def test_patch_sets_value():
    d = design()
    result = apply_patch(d, patch("/analysis/model", "Adjusted mixed model"), source_ids={"S1"})
    assert result.analysis.model == "Adjusted mixed model"
    assert d.analysis.model == "Mixed model with class random intercept"


@pytest.mark.parametrize("path", ["/claim/margin", "/claim/margin/value", "/claim", "/"])
def test_patch_rejects_researcher_owned_claim(path):
    d = design()
    if path == "/claim":
        value = d.claim.model_dump()
        value["margin"]["value"] = 5
    elif path == "/":
        value = d.model_dump()
        value["claim"]["margin"]["value"] = 5
    else:
        value = 5
    with pytest.raises(PatchError, match="researcher-owned"):
        apply_patch(d, patch(path, value), source_ids={"S1"})


def test_patch_rejects_origin_edit():
    with pytest.raises(PatchError, match="origin"):
        apply_patch(
            design(),
            patch("/claim/estimand/origin", "designer_default"),
            source_ids={"S1"},
        )


def test_patch_rejects_unknown_source_ref():
    with pytest.raises(PatchError, match="S9"):
        apply_patch(
            design(),
            patch(
                "/sampling/cluster/icc", {"value": 0.2, "provenance": "source", "source_ref": "S9"}
            ),
            source_ids={"S1"},
        )


@pytest.mark.parametrize("path", ["/nope/x", "/conditions/99/name", "/conditions/-1/name"])
def test_patch_bad_pointer(path):
    with pytest.raises(PatchError):
        apply_patch(design(), patch(path, "x"), source_ids={"S1"})


def test_diff_leaf_paths():
    before, after = design(), design()
    before.analysis.alpha = 0.05
    assert design_diff(before, after) == [
        {"path": "/analysis/alpha", "before": 0.05, "after": 0.025},
    ]


def test_hash_stable():
    assert design_hash(design()) == design_hash(design())
    d = design()
    d.analysis.alpha = 0.05
    assert design_hash(d) != design_hash(design())


def test_set_claim_researcher():
    d = design()
    result = set_claim(d, "margin", 2.0, "researcher")
    assert (result.claim.margin.value, result.claim.margin.origin) == (2.0, "researcher")
    assert d.claim.margin.value == 3
