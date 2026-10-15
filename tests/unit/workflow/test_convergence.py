from experiment_design.contracts import AgentOutput, Finding, Resolution
from experiment_design.workflow.convergence import converge_final, due_later, previous_majors

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


def test_missing_resolution_counts_as_maintained_and_resolved_does_not():
    prior = previous_majors([_finding("F1", anchor="Costs")])
    silent = converge_final(_output(_finding("F9", anchor="Costs")), prior, OLD, NEW)
    assert silent.findings[0].severity == "MAJOR"
    resolved = _output(
        _finding("F9", anchor="Costs"),
        resolutions=[Resolution(finding_id="P1", status="resolved", reason="fixed")],
    )
    assert converge_final(resolved, prior, OLD, NEW).findings[0].severity == "MINOR"


def test_later_findings_never_block():
    out = _output(_finding("F1", due="later"), _finding("F2"))
    due_later(out)
    assert out.findings[0].severity == "MINOR"
    assert out.findings[0].issue == "[for execution] Issue F1"
    assert out.findings[1].severity == "MAJOR"
