from pathlib import Path

from experiment_design.package import snapshot_sources


def test_example_preserves_draft_evidence_and_researcher_constraints():
    path = Path(__file__).parents[1] / "hypothesis.example.md"
    snapshot = snapshot_sources([path])
    source = snapshot.artifacts[0]
    assert source.text == path.read_bytes().decode("utf-8")
    assert "DRAFT" in source.text
    assert "researcher" in source.text.lower()
    assert "non-inferior" in source.text.lower()
    assert "3" in source.text and "40" in source.text
    assert "assum" in source.text.lower()
