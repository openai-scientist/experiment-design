"""Read researcher-written hypothesis Markdown without filling scientific gaps."""

import hashlib
import json
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from experiment_design.contracts import (
    CLAIM_FIELDS,
    Assumption,
    Hypothesis,
    Inference,
    Outcome,
    Quantity,
    ResearcherRequest,
    ScientificPackage,
    Source,
)
from experiment_design.patch import package_claim

SECTIONS = (
    "Hypothesis",
    "Outcome",
    "Inference",
    "Scope",
    "Constraints",
    "Budget",
    "Limitations",
    "Assumptions",
    "Sources",
)
KEYS = {
    "hypothesis": {"prediction", "failure condition", "exposure", "method"},
    "outcome": {"name", "unit", "kind", "direction"},
    "inference": {"contrast", "margin", "estimand"},
}


class PackageFormatError(ValueError):
    pass


def load_package(path: Path) -> ScientificPackage:
    path = Path(path)
    sections: dict[str, list[tuple[int, str]]] = {}
    section, question = None, None
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    for number, line in enumerate(lines, 1):
        if line.startswith("# "):
            if question is None:
                question = line[2:].strip()
            continue
        if line.startswith("## "):
            heading = line[3:].strip()
            parts = heading.split(maxsplit=1)
            section = parts[0].casefold() if parts else ""
            if section not in {s.casefold() for s in SECTIONS}:
                raise PackageFormatError(
                    f"line {number}: unknown section {heading!r}; allowed: {', '.join(SECTIONS)}"
                )
            sections.setdefault(section, [])
            if len(parts) > 1:
                sections[section].append((number, parts[1]))
            continue
        if section is not None:
            sections[section].append((number, line))

    def error(number, message):
        raise PackageFormatError(f"line {number}: {message}")

    def paragraph(name):
        result = []
        for _, line in sections.get(name, []):
            if not line.strip() and result:
                break
            if line.startswith("- "):
                break
            if line.strip():
                result.append(line.strip())
        return " ".join(result) or None

    def keyed(name):
        result = {}
        for number, line in sections.get(name, []):
            if not line.startswith("- "):
                continue
            key, sep, value = line[2:].partition(":")
            key = key.strip().casefold()
            if not sep or key not in KEYS[name]:
                error(number, f"unknown key {key!r}; allowed: {', '.join(sorted(KEYS[name]))}")
            field = {"failure condition": "failure_condition", "contrast": "contrast_type"}.get(
                key, key
            )
            value = value.strip()
            if key == "margin":
                try:
                    value = float(value)
                except ValueError:
                    error(number, "margin must be numeric")
            choices = {
                "kind": {"continuous", "binary"},
                "direction": {"increase", "decrease"},
                "contrast": {"superiority", "non_inferiority", "superiority_by_margin"},
            }
            if key in choices:
                value = value.casefold()
                if value not in choices[key]:
                    error(number, f"unsupported {key}: {value!r}")
            result[field] = value
        return result

    def bullets(name):
        return [line[2:] for _, line in sections.get(name, []) if line.startswith("- ")]

    statement = paragraph("hypothesis")
    if not question:
        error(1, "a research question H1 is required")
    if not statement:
        error(1, "a Hypothesis statement is required")
    sources = []
    source_ids = set()
    source_heading, source_body, source_line = None, [], 1

    def finish_source():
        if source_heading is None:
            return
        sid, title = source_heading
        if sid in source_ids:
            error(source_line, f"duplicate source {sid}")
        source_ids.add(sid)
        text = "\n".join(source_body).strip()
        kind = "note"
        body_lines = text.splitlines()
        if body_lines and body_lines[0].casefold().startswith("kind:"):
            kind = body_lines.pop(0).partition(":")[2].strip().casefold()
            if kind not in {"paper", "dataset", "note"}:
                error(source_line + 1, f"unsupported source kind {kind}")
            text = "\n".join(body_lines).strip()
        sources.append(Source(id=sid, title=title, kind=kind, text=text))

    for number, line in sections.get("sources", []):
        if line.startswith("### "):
            finish_source()
            match = re.fullmatch(r"### (S[0-9]+):\s*(.+)", line)
            if not match:
                error(number, "source heading requires an S<n>: id and title")
            source_heading, source_body, source_line = match.groups(), [], number
        elif source_heading is not None:
            source_body.append(line)
        elif line.strip():
            error(number, "source body requires an S<n>: heading")
    finish_source()
    assumptions, assumption_ids = [], set()
    for number, line in sections.get("assumptions", []):
        if not line.strip():
            continue
        match = re.fullmatch(r"- (A[0-9]+):\s*(.+)", line)
        if not match:
            error(number, "assumption requires an A<n>: id")
        aid, text = match.groups()
        if aid in assumption_ids:
            error(number, f"duplicate assumption {aid}")
        assumption_ids.add(aid)
        quantity = None
        brace = re.search(r"(\{.*\})\s*$", text)
        if brace:
            try:
                data = yaml.safe_load(brace.group(1))
                if not isinstance(data, dict):
                    error(number, "assumption quantity must be a YAML mapping")
                ref = data.pop("source", None)
                if ref is not None and ref not in source_ids:
                    error(number, f"unknown assumption source {ref}")
                quantity = Quantity(
                    **data,
                    provenance="source" if ref else "assumption",
                    source_ref=ref,
                )
            except (yaml.YAMLError, ValidationError, TypeError) as exc:
                error(number, f"invalid assumption quantity: {exc}")
            text = text[: brace.start()].strip()
        assumptions.append(Assumption(id=aid, statement=text, quantity=quantity))
    outcome = keyed("outcome")
    if "outcome" in sections and not outcome.get("name"):
        error(sections["outcome"][0][0] if sections["outcome"] else 1, "Outcome needs a name")
    try:
        return ScientificPackage(
            id=path.stem,
            research_question=question,
            hypothesis=Hypothesis(
                statement=statement, **keyed("hypothesis"), limitations=bullets("limitations")
            ),
            outcome=Outcome(**outcome) if "outcome" in sections else None,
            inference=Inference(**keyed("inference")) if "inference" in sections else None,
            scope=paragraph("scope"),
            budget=paragraph("budget"),
            constraints=bullets("constraints"),
            assumptions=assumptions,
            sources=sources,
        )
    except ValidationError as exc:
        error(len(lines), f"invalid hypothesis input: {exc}")


def package_hash(package: ScientificPackage) -> str:
    raw = json.dumps(package.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def intake_gaps(package: ScientificPackage) -> list[str]:
    values = package_claim(package)
    return [
        field
        for field in CLAIM_FIELDS
        if values[field] is None
        and (
            field != "margin"
            or values["contrast_type"]
            in {
                "non_inferiority",
                "superiority_by_margin",
            }
        )
    ]


def all_sources(package: ScientificPackage, requests: list[ResearcherRequest]) -> list[Source]:
    return list(package.sources) + [
        Source(id=f"researcher:{r.id}", title=r.question, kind="researcher_input", text=r.answer)
        for r in requests
        if r.kind == "input" and r.status == "answered" and r.answer is not None
    ]
