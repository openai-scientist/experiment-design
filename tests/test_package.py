from pathlib import Path

from experiment_design.contracts import ResearcherRequest, Source
from experiment_design.package import all_sources, snapshot_sources
from experiment_design.science.evidence import lookup_evidence
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


def test_lookup_evidence_ranks_and_cites(tmp_path):
    sources = [
        Source(id="S1", title="Brief", kind="note", text="Scores are available."),
        Source(
            id="S2",
            title="Data",
            kind="note",
            text="Standard deviation of term scores is 10 points.",
        ),
    ]
    hits = lookup_evidence("standard deviation term scores", sources)
    assert [hit.source_id for hit in hits] == ["S2", "S1"]
    assert hits[0].excerpt == "Standard deviation of term scores is 10 points."


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
