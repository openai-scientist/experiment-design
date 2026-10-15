"""Code rules that keep review rounds convergent and keep execution-time evidence off the gate."""

import re

from experiment_design.contracts import AgentOutput, Finding

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _sections(plan: str) -> dict[str, str]:
    """Normalised heading -> text of its section, subsections included."""
    lines = plan.splitlines()
    heads = [
        (i, len(m[1]), _norm(m[2])) for i, line in enumerate(lines) if (m := _HEADING.match(line))
    ]
    result = {}
    for n, (start, level, title) in enumerate(heads):
        end = next((i for i, lv, _ in heads[n + 1 :] if lv <= level), len(lines))
        result[title] = "\n".join(lines[start:end])
    return result


def _matches(anchor: str, headings: set[str]) -> set[str]:
    wanted = _norm(anchor)
    if wanted in headings:
        return {wanted}
    return {h for h in headings if wanted in h or h in wanted}


def due_later(output: AgentOutput) -> None:
    """Findings about execution-time evidence never block a plan."""
    for finding in output.findings:
        if finding.due == "later":
            finding.severity = "MINOR"
            if not finding.issue.startswith("[for execution] "):
                finding.issue = "[for execution] " + finding.issue


def previous_majors(findings: list[Finding]) -> list[dict]:
    """The earlier MAJOR findings, relabelled P1.. so they cannot collide with new ids."""
    majors = [f for f in findings if f.severity == "MAJOR"]
    return [
        {"id": f"P{n}", "anchor": f.anchor, "issue": f.issue, "consequence": f.consequence}
        for n, f in enumerate(majors, 1)
    ]


def converge_final(
    output: AgentOutput, prior: list[dict], old_plan: str, new_plan: str
) -> AgentOutput:
    """A new MAJOR needs a section the plan diff changed, unless it maintains an earlier one.

    A previous MAJOR without a ``resolved`` resolution counts as maintained.
    """
    resolved = {r.finding_id for r in output.resolutions if r.status == "resolved"}
    maintained = {_norm(p["anchor"]) for p in prior if p["id"] not in resolved}
    old, new = _sections(old_plan), _sections(new_plan)
    changed = {title for title, body in new.items() if old.get(title) != body}
    for finding in output.findings:
        if finding.severity != "MAJOR" or not finding.anchor.strip():
            continue
        if _norm(finding.anchor) in maintained:
            continue
        matched = _matches(finding.anchor, set(new))
        if matched and not matched & changed:
            finding.severity = "MINOR"
            finding.issue = "[unchanged] " + finding.issue
    return output
