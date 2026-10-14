import pytest
from pydantic import ValidationError

from experiment_design.agents.prompts import prompt, snapshot
from experiment_design.contracts import Finding, Profile
from experiment_design.packs import load_pack


def test_pack_addenda_and_review_rubric_preserve_legacy_prompt():
    legacy = snapshot()
    assert prompt("design", None) == legacy["design"]
    pack = load_pack(Profile(domain="ml"))
    review = prompt("skeptic", pack)
    for category in ("leakage", "baseline", "control", "statistics", "reproducibility"):
        assert f"[{category}]" in review
    assert pack.role_addenda["skeptic"] in review
    assert prompt("design", pack).startswith(legacy["design"])
    assert snapshot(pack)["pack_layers"] == "base,ml"


def test_finding_category_is_validated_and_old_output_still_parses():
    values = dict(id="x", severity="MAJOR", issue="gap", consequence="bias", suggestion="fix")
    assert Finding(**values).category == "other"
    assert Finding(**values, category="leakage").category == "leakage"
    assert Finding(**values, category="framing").category == "framing"
    with pytest.raises(ValidationError):
        Finding(**values, category="invented")
