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


@pytest.mark.parametrize("role", ["methodologist", "skeptic", "final_check"])
def test_reviewers_mark_an_input_conflict_as_unfixable_by_revision(role):
    # Live 2026-10-16: an H1-only selection against a two-hypothesis brief was raised as
    # 'fix', so the engine reframed twice and stopped without a reviewed plan.
    text = prompt(role, None)
    assert "conflict between the supplied inputs" in text
    assert "fewer supplied hypotheses than the research_brief requires" in text
    assert "conflict between the supplied inputs" not in prompt("design", None)


@pytest.mark.parametrize("role", ["design", "methodologist", "skeptic", "final_check"])
def test_designers_and_reviewers_share_one_rule_for_analysis_decisions(role):
    # Live 2026-10-16: the designer labelled a confirmatory gate and the bootstrap seed
    # "Proposal — confirm before launch" while reviewers and the PI's Critic required them
    # frozen; the plan was revised three times and the run stopped.
    text = " ".join(prompt(role, None).split())
    assert "is not an implementation detail: the plan fixes it with a concrete value" in text
