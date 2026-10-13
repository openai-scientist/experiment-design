from experiment_design.contracts import Source
from experiment_design.science.evidence import lookup_evidence


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
