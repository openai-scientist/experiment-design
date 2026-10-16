import json
from types import SimpleNamespace

from experiment_design.publication.plan import _open_findings


class _Store:
    """Only stage 4 holds a valid review; stage 5 may have been edited after it completed."""

    def __init__(self, path, *, tampered):
        self.path = path
        self.state = SimpleNamespace(attempt=1)
        self._tampered = tampered

    def valid(self, stage):
        return stage == 4

    def tampered(self, stage):
        return stage == 5 and self._tampered

    def bound_findings(self, stage, name, attempt):
        return [json.loads((self.path / "stage-04" / name).read_text(encoding="utf-8"))]


def _stage_4(tmp_path):
    folder = tmp_path / "stage-04"
    folder.mkdir()
    record = {"finding_id": "F1", "severity": "MAJOR", "issue": "Gap"}
    (folder / "methodologist_review.json").write_text(json.dumps(record), encoding="utf-8")


def test_the_critics_findings_stay_open_when_no_final_review_exists(tmp_path):
    _stage_4(tmp_path)
    found = _open_findings(_Store(tmp_path, tampered=False))
    assert [f["finding_id"] for f in found] == ["F1"]


def test_a_tampered_final_review_reports_no_open_findings(tmp_path):
    _stage_4(tmp_path)
    assert _open_findings(_Store(tmp_path, tampered=True)) == []


def test_the_open_findings_section_is_english():
    from experiment_design.publication.plan import _open_findings_section

    finding = {
        "finding_id": "F1",
        "severity": "MAJOR",
        "issue": "Gap.",
        "consequence": "Bad.",
        "suggestion": "Fix.",
    }
    text = _open_findings_section([finding])
    assert "## Open findings" in text and "Consequence: Bad." in text and "Suggestion: Fix." in text
    assert "No specific finding" in _open_findings_section([])
