"""Shipped examples stay generic: no mock constraints, budgets or removed samples."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HYPOTHESES = [ROOT / "hypotheses.example.md", ROOT / "hypotheses.ml.example.md"]


def test_hypothesis_examples_have_two_hypotheses():
    for path in HYPOTHESES:
        text = path.read_text(encoding="utf-8")
        assert len(re.findall(r"^## H\d+: ", text, flags=re.M)) >= 2, path.name


def test_hypothesis_examples_have_no_mock_constraints_or_budget():
    for path in HYPOTHESES:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^## (Constraints|Budget)\b", text, flags=re.M), path.name


def test_removed_grade7_example_is_gone_and_new_examples_exist():
    examples = ROOT / "docs" / "examples"
    assert not list(examples.glob("grade7*"))
    for name in ("ml-experiment-plan.example.md", "se-experiment-plan.example.md"):
        text = (examples / name).read_text(encoding="utf-8")
        assert "agents have not run" in text


def test_agents_md_names_registry_rule():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "science/registry.py" in text
    assert "independent-pooled-t/equal-cluster-t" not in text
