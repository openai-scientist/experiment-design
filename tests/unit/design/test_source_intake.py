import hashlib
from pathlib import Path

import pytest

from experiment_design.contracts import ResearcherRequest
from experiment_design.design.intake import PackageFormatError, all_sources, snapshot_sources
from tests.support.builders import package


def test_load_example_file():
    path = Path("hypothesis.example.md")
    snapshot = snapshot_sources([path])
    assert snapshot.artifacts[0].text == path.read_bytes().decode("utf-8")
    assert snapshot.artifacts[0].metadata["role"] == "input"


def test_free_text_bullets_keep_colons(tmp_path):
    path = tmp_path / "idea.md"
    text = "A question without a required template\n## Arbitrary domain heading\n- time: one term\n"
    path.write_bytes(text.encode("utf-8"))
    assert snapshot_sources([path]).artifacts[0].text == text


def test_all_sources_includes_answered_inputs():
    req = ResearcherRequest(
        id="R1",
        run_id="r1",
        kind="input",
        question="District SD?",
        raised_by="design",
        status="answered",
        answer="10 points",
    )
    sources = all_sources(package(), [req, req.model_copy(update={"id": "R2", "status": "open"})])
    assert [(s.id, s.title, s.text) for s in sources[1:]] == [
        ("researcher:R1", "District SD?", "10 points"),
    ]


def test_snapshot_preserves_freeform_bom_duplicates_and_missing_attachment(tmp_path):
    text = "\ufeffWhy feedback?\r\n### Any heading\r\nMargin 3 points. [Data](missing.csv)\r\n"
    main = tmp_path / "study.md"
    other = tmp_path / "second.md"
    main.write_bytes(text.encode("utf-8"))
    other.write_bytes(text.encode("utf-8"))
    snapshot = snapshot_sources([main, other])
    assert [a.text for a in snapshot.artifacts] == [text, text]
    assert snapshot.artifacts[0].sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert snapshot.artifacts[0].ref != snapshot.artifacts[1].ref
    assert "missing.csv" in " ".join(snapshot.intake_issues)
    with pytest.raises(PackageFormatError, match="read"):
        snapshot_sources([tmp_path / "absent.md"])
