"""Code rules that keep review rounds convergent and keep execution-time evidence off the gate."""

import re

from experiment_design.contracts import AgentOutput, Finding

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _sections(plan: str) -> dict[str, list[str]]:
    """Heading path (``a > b``) -> texts of its sections, subsections included."""
    lines = plan.splitlines()
    heads = [
        (i, len(m[1]), _norm(m[2])) for i, line in enumerate(lines) if (m := _HEADING.match(line))
    ]
    result: dict[str, list[str]] = {}
    stack: list[tuple[int, str]] = []
    for n, (start, level, title) in enumerate(heads):
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        end = next((i for i, lv, _ in heads[n + 1 :] if lv <= level), len(lines))
        path = " > ".join(t for _, t in stack)
        result.setdefault(path, []).append("\n".join(lines[start:end]))
    return result


def _matches(anchor: str, paths: set[str]) -> set[str]:
    wanted = _norm(anchor)
    if wanted in paths:
        return {wanted}
    last = {p for p in paths if p.rsplit(" > ", 1)[-1] == wanted}
    if last:
        return last
    return {p for p in paths if wanted in p or p in wanted}


def due_later(output: AgentOutput) -> None:
    """Findings about execution-time evidence never block a plan."""
    for finding in output.findings:
        # A design flaw cannot wait for execution.
        if finding.due == "later" and finding.category not in {"leakage", "baseline", "control"}:
            was_major = finding.severity == "MAJOR"
            finding.severity = "MINOR"
            if was_major and not finding.issue.startswith("[for execution] "):
                finding.issue = "[for execution] " + finding.issue


def severity_adjustments(output: AgentOutput) -> str:
    """A markdown section listing the findings that code rules demoted from MAJOR."""
    lines = []
    for f in output.findings:
        for tag in ("[unchanged]", "[for execution]"):
            if f.severity == "MINOR" and f.issue.startswith(tag + " "):
                lines.append(f"- {f.id}: MAJOR → MINOR {tag}")
    if not lines:
        return ""
    body = "\n".join(lines)
    return f"\n\n## Severity adjustments\n\n{body}\n"


def previous_majors(findings: list[Finding]) -> list[dict]:
    """The earlier MAJOR findings, relabelled P1.. so they cannot collide with new ids."""
    majors = [f for f in findings if f.severity == "MAJOR"]
    return [
        {
            "id": f"P{n}",
            "anchor": f.anchor,
            "issue": f.issue,
            "consequence": f.consequence,
            "kind": f.kind,
        }
        for n, f in enumerate(majors, 1)
    ]


def converge_final(
    output: AgentOutput, prior: list[dict], old_plan: str, new_plan: str
) -> AgentOutput:
    """A new MAJOR needs a section the plan diff changed, unless it maintains an earlier one.

    A previous MAJOR without a ``resolved`` resolution counts as maintained. A ``resolved``
    resolution is accepted only when the anchor section changed. A maintained previous MAJOR
    that no new finding covers is added again as MAJOR.
    """
    old, new = _sections(old_plan), _sections(new_plan)
    changed = {path for path, bodies in new.items() if old.get(path) != bodies}
    resolved = {r.finding_id for r in output.resolutions if r.status == "resolved"}
    said_maintained = {r.finding_id for r in output.resolutions if r.status == "maintained"}
    majors = [f for f in output.findings if f.severity == "MAJOR"]
    open_priors = []
    for p in prior:
        if p["id"] in resolved:
            matched = _matches(p["anchor"], set(new)) if p["anchor"].strip() else set()
            if not matched or matched & changed:
                continue
        open_priors.append(p)
    anchors = {_norm(p["anchor"]) for p in open_priors if p["anchor"].strip()}
    for p in open_priors:
        covered = any(
            f.id == p["id"] or (bool(p["anchor"].strip()) and _norm(f.anchor) == _norm(p["anchor"]))
            for f in majors
        )
        if not covered:
            output.findings.append(
                Finding(
                    id=p["id"],
                    severity="MAJOR",
                    issue=f"[maintains {p['id']}] {p['issue']}",
                    consequence=p["consequence"],
                    suggestion="Resolve the earlier finding.",
                    anchor=p["anchor"],
                    kind=p.get("kind", "fix"),
                )
            )
    for finding in majors:
        if not finding.anchor.strip():
            continue
        if finding.id in said_maintained or _norm(finding.anchor) in anchors:
            continue
        matched = _matches(finding.anchor, set(new))
        if matched and not matched & changed:
            finding.severity = "MINOR"
            finding.issue = "[unchanged] " + finding.issue
    return output
