from pathlib import Path

import pytest
from conftest import package

from experiment_design.contracts import Quantity, ResearcherRequest, Source
from experiment_design.package import PackageFormatError, all_sources, intake_gaps, load_package
from experiment_design.science.evidence import lookup_evidence


def input_file(tmp_path, extra):
    p = tmp_path / "hypothesis.md"
    p.write_text("# Question\n\n## Hypothesis\nStatement\n\n" + extra, encoding="utf-8")
    return p


def test_load_example_file():
    p = load_package(Path("hypothesis.example.md"))
    assert (p.inference.margin, p.inference.contrast_type, p.outcome.direction) == (
        3.0,
        "non_inferiority",
        "increase",
    )
    assert "10 points" in p.sources[0].text
    assert p.assumptions[0].quantity == Quantity(
        value=25,
        unit="students",
        provenance="assumption",
    )


def test_minimal_file_reports_gaps():
    gaps = intake_gaps(load_package(Path("tests/fixtures/minimal.md")))
    assert "primary_outcome" in gaps and "contrast_type" in gaps


def test_free_text_bullets_keep_colons(tmp_path):
    p = load_package(input_file(tmp_path, "## Constraints\n- time: one term\n"))
    assert p.constraints == ["time: one term"]


def test_unknown_assumption_source_rejected(tmp_path):
    path = input_file(tmp_path, "## Assumptions\n- A1: SD {value: 10, unit: points, source: S9}\n")
    with pytest.raises(PackageFormatError, match="S9"):
        load_package(path)


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


def test_unknown_section_rejected(tmp_path):
    with pytest.raises(PackageFormatError, match="line 6.*Outcome"):
        load_package(input_file(tmp_path, "## Outcomes\n- name: score\n"))


def test_unknown_key_rejected(tmp_path):
    with pytest.raises(PackageFormatError, match="margn"):
        load_package(input_file(tmp_path, "## Inference\n- margn: 3\n"))


def test_bad_margin_rejected(tmp_path):
    with pytest.raises(PackageFormatError, match="line 7"):
        load_package(input_file(tmp_path, "## Inference\n- margin: three\n"))


def test_assumption_with_source_ref(tmp_path):
    p = load_package(
        input_file(
            tmp_path,
            "## Assumptions\n- A1: SD {value: 10, unit: points, source: S1}\n"
            "## Sources\n### S1: Records\n10 points\n",
        )
    )
    assert (p.assumptions[0].quantity.provenance, p.assumptions[0].quantity.source_ref) == (
        "source",
        "S1",
    )


def test_intake_gaps_full_package_empty():
    assert intake_gaps(package()) == []


def test_lookup_evidence_ranks_and_cites(tmp_path):
    p = load_package(
        input_file(
            tmp_path,
            "## Sources\n### S1: Brief\nScores are available.\n"
            "### S2: Data\nStandard deviation of term scores is 10 points.\n",
        )
    )
    hits = lookup_evidence("standard deviation term scores", p.sources)
    assert [hit.source_id for hit in hits] == ["S2", "S1"]
    assert hits[0].excerpt == "Standard deviation of term scores is 10 points."


def test_lookup_evidence_no_match_empty():
    assert (
        lookup_evidence(
            "unrelated phrase",
            [
                Source(id="S1", title="Note", kind="note", text="District scores"),
            ],
        )
        == []
    )
