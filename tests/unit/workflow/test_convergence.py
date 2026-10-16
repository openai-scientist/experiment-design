from experiment_design.contracts import AgentOutput, Finding, Resolution
from experiment_design.workflow.convergence import (
    converge_final,
    due_later,
    previous_majors,
    severity_adjustments,
)

OLD = "# Plan\n## Design\nold design\n## Analysis\nsame\n## Costs\nsame\n"
NEW = "# Plan\n## Design\nnew design\n## Analysis\nsame\n## Costs\nsame\n"


def _finding(id, severity="MAJOR", anchor="", **extra):
    return Finding(
        id=id, severity=severity, issue="Issue " + id, consequence="c", suggestion="s",
        anchor=anchor, **extra,
    )  # fmt: skip


def _output(*findings, resolutions=()):
    return AgentOutput(markdown="m", findings=list(findings), resolutions=list(resolutions))


def test_new_major_on_unchanged_section_becomes_minor():
    out = converge_final(_output(_finding("F1", anchor="Analysis")), [], OLD, NEW)
    assert out.findings[0].severity == "MINOR"
    assert out.findings[0].issue.startswith("[unchanged] ")


def test_new_major_on_changed_section_stays_major():
    out = converge_final(_output(_finding("F1", anchor="Design")), [], OLD, NEW)
    assert out.findings[0].severity == "MAJOR"


def test_unanchored_or_unresolvable_major_stays_major():
    findings = [_finding("F1"), _finding("F2", anchor="No such section")]
    out = converge_final(_output(*findings), [], OLD, NEW)
    assert [f.severity for f in out.findings] == ["MAJOR", "MAJOR"]


def test_maintained_previous_major_stays_major_on_unchanged_section():
    prior = previous_majors([_finding("F1", anchor="Costs"), _finding("F2", "MINOR")])
    assert [p["id"] for p in prior] == ["P1"]
    kept = _output(
        _finding("F9", anchor="Costs"),
        resolutions=[Resolution(finding_id="P1", status="maintained", reason="still open")],
    )
    assert converge_final(kept, prior, OLD, NEW).findings[0].severity == "MAJOR"


def test_missing_resolution_counts_as_maintained_and_unfounded_resolved_is_rejected():
    prior = previous_majors([_finding("F1", anchor="Costs")])
    silent = converge_final(_output(_finding("F9", anchor="Costs")), prior, OLD, NEW)
    assert silent.findings[0].severity == "MAJOR"
    resolved = _output(
        _finding("F9", anchor="Costs"),
        resolutions=[Resolution(finding_id="P1", status="resolved", reason="fixed")],
    )
    # The claim is rejected: the anchor section did not change.
    assert converge_final(resolved, prior, OLD, NEW).findings[0].severity == "MAJOR"


def test_later_findings_never_block():
    out = _output(_finding("F1", due="later"), _finding("F2"))
    due_later(out)
    assert out.findings[0].severity == "MINOR"
    assert out.findings[0].issue == "[for execution] Issue F1"
    assert out.findings[1].severity == "MAJOR"


DUP_OLD = "# Plan\n## E1\n### Metrics\nm1\n## E2\n### Metrics\nm2\n"
DUP_NEW = "# Plan\n## E1\n### Metrics\nm1\n## E2\n### Metrics\nchanged\n"


def test_duplicate_headings_are_kept_apart_and_any_changed_match_counts():
    own = converge_final(_output(_finding("F1", anchor="e1 > metrics")), [], DUP_OLD, DUP_NEW)
    assert own.findings[0].severity == "MINOR"
    shared = converge_final(_output(_finding("F1", anchor="Metrics")), [], DUP_OLD, DUP_NEW)
    assert shared.findings[0].severity == "MAJOR"


def test_maintained_resolution_by_id_keeps_a_new_major():
    prior = previous_majors([_finding("F1", anchor="Costs")])
    out = _output(
        _finding("P1", anchor="Analysis"),
        resolutions=[Resolution(finding_id="P1", status="maintained", reason="open")],
    )
    assert converge_final(out, prior, OLD, NEW).findings[0].severity == "MAJOR"


def test_uncovered_maintained_major_is_added_again():
    prior = previous_majors([_finding("F1", anchor="Costs")])
    out = converge_final(_output(), prior, OLD, NEW)
    assert [(f.id, f.severity, f.issue) for f in out.findings] == [
        ("P1", "MAJOR", "[maintains P1] Issue F1")
    ]


def test_resolved_claim_on_unchanged_section_is_rejected_but_changed_one_accepted():
    prior = previous_majors([_finding("F1", anchor="Costs"), _finding("F2", anchor="Design")])
    claims = [
        Resolution(finding_id="P1", status="resolved", reason="fixed"),
        Resolution(finding_id="P2", status="resolved", reason="fixed"),
    ]
    out = converge_final(_output(resolutions=claims), prior, OLD, NEW)
    assert [f.issue for f in out.findings] == ["[maintains P1] Issue F1"]


def test_severity_adjustments_lists_each_demotion():
    out = converge_final(_output(_finding("F1", anchor="Analysis")), [], OLD, NEW)
    text = severity_adjustments(out)
    assert "## Severity adjustments" in text and "F1: MAJOR → MINOR [unchanged]" in text
    assert severity_adjustments(_output(_finding("F2", anchor="Design"))) == ""


def test_design_flaws_are_not_deferred_to_execution():
    out = _output(
        _finding("F1", due="later", category="leakage"),
        _finding("F2", due="later", category="control"),
        _finding("F3", due="later", category="baseline"),
        _finding("F4", severity="MINOR", due="later", category="statistics"),
    )
    due_later(out)
    assert [f.severity for f in out.findings] == ["MAJOR", "MAJOR", "MAJOR", "MINOR"]
    assert out.findings[0].issue == "Issue F1"
    # A finding that was MINOR already is not listed as a demotion.
    assert out.findings[3].issue == "Issue F4"
    assert severity_adjustments(out) == ""


def test_a_comment_in_a_code_block_is_not_a_heading():
    from experiment_design.workflow.convergence import _sections

    lines = ["# Plan", "## Method", "```python", "# set seed", "```", "after", "## Analysis", "x"]
    sections = _sections("\n".join(lines))
    assert sorted(sections) == ["plan", "plan > analysis", "plan > method"]
    assert "after" in sections["plan > method"][0]
