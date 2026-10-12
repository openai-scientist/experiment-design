import pytest

from experiment_design.contracts import Patch, PatchOp
from experiment_design.patch import (
    PatchError,
    apply_patch,
    design_diff,
    design_hash,
    initial_design,
)
from tests.support.builders import design, package


def research_context():
    from experiment_design.contracts import FieldAnnotation, ResearchContext

    return ResearchContext(
        id="context",
        revision=1,
        source_snapshot_id="source-1",
        package=package(),
        field_annotations={
            p: FieldAnnotation(status="source-stated")
            for p in (
                "/measurement/primary_metric",
                "/conditions",
                "/analysis/decision_rule",
                "/analysis/stopping",
                "/analysis/model",
            )
        },
    )


@pytest.mark.parametrize(
    "path,value",
    [
        ("/measurement/primary_metric/name", "different outcome"),
        ("/conditions/0/description", "Different intervention"),
        ("/analysis/decision_rule", "Choose a different conclusion"),
        ("/analysis/stopping", "Stop when significant"),
        ("/analysis/model", "Different estimand model"),
    ],
)
def test_scientific_commitments_outside_claim_are_protected(path, value):
    with pytest.raises(PatchError, match="researcher"):
        apply_patch(design(), patch(path, value), source_ids={"S1"}, context=research_context())


def test_margin_preview_is_read_only_and_has_no_commit_authority():
    from experiment_design.patch import preview_design

    original = design()
    proposed = patch("/claim/margin", 2)
    preview = preview_design(original, proposed, context=research_context(), source_ids={"S1"})
    assert preview.claim.margin.value == 2
    assert preview.claim.margin.origin == "designer_default"
    assert original.claim.margin.value == 3
    with pytest.raises(PatchError, match="researcher"):
        apply_patch(original, proposed, source_ids={"S1"}, context=research_context())


def test_author_argument_does_not_grant_researcher_rights():
    with pytest.raises(PatchError, match="authorization"):
        apply_patch(design(), patch("/claim/margin", 2), source_ids={"S1"}, author="researcher")


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


def test_patch_rejects_unattested_computed_input():
    with pytest.raises(PatchError, match="unattested computed quantity"):
        apply_patch(
            design(),
            patch("/scenarios/0/sd", {"value": 1, "unit": "points", "provenance": "computed"}),
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


def test_unannotated_understanding_cannot_become_researcher_owned():
    from experiment_design.contracts import ResearchContext
    from experiment_design.patch import initial_design

    context = ResearchContext(
        id="context", revision=1, source_snapshot_id="source", package=package()
    )
    committed = initial_design(context.package, design(), context=context)
    assert committed.claim.estimand.origin == "designer_default"
    assert committed.claim.margin.origin == "designer_default"


def test_source_specified_sap_cannot_be_replaced_in_initial_draft():
    from experiment_design.contracts import FieldAnnotation, ResearchContext
    from experiment_design.patch import initial_design

    p = package()
    p.hypothesis.method = "Mixed model with class random intercept"
    context = ResearchContext(
        id="context",
        revision=1,
        source_snapshot_id="source",
        package=p,
        field_annotations={
            "/analysis/model": FieldAnnotation(
                status="source-stated", source_ref="S1", source_span=p.hypothesis.method
            )
        },
    )
    from tests.support.scenarios import supported_design

    with pytest.raises(PatchError, match="researcher-owned"):
        initial_design(p, supported_design(), context=context)
