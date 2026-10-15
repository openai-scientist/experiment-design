"""Turn the contract inputs of a run into the Markdown files the workflow reads."""

import hashlib
import json
import logging
import re
from pathlib import Path

import httpx

from experiment_design.api.contract_models import (
    I2H_EVIDENCE,
    I2H_HYPOTHESES,
    REVIEW_FINDINGS,
    SELECTION_HYPOTHESIS_IDS,
    SHA256_MISMATCH,
    VALIDATION_ERROR,
    RunCreateRequest,
)

_SKIPPED_FIELDS = {"id", "statement"}
_LOGGER = logging.getLogger(__name__)
_TRIM = 300
_HYPOTHESIS_ANCHOR = re.compile(r"hypothesis:([A-Za-z0-9_.-]+)")


class InputError(ValueError):
    def __init__(self, message: str, code: str = VALIDATION_ERROR):
        super().__init__(message)
        self.code = code


def _value(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "".join(f"\n  - {item}" for item in value)
    return json.dumps(value, ensure_ascii=False)


def _trim(value: object) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= _TRIM else text[: _TRIM - 1].rstrip() + "…"


def _evidence_lines(card: dict) -> list[str]:
    year = f" ({_trim(card['year'])})" if card.get("year") else ""
    link = _trim(card.get("doi") or card.get("url") or "")
    lines = [f"- {_trim(card['title'])}{year}" + (f" — {link}" if link else "")]
    for field in ("data", "metrics", "findings"):
        if card.get(field):
            lines.append(f"  - {field.capitalize()}: {_trim(card[field])}")
    return lines


def _render(hypotheses: list[dict], cards: dict[str, dict], issues: list[str]) -> str:
    blocks = ["# Hypotheses"]
    for hypothesis in hypotheses:
        lines = [f"## {hypothesis['id']}: {hypothesis['statement']}", ""]
        lines += [
            f"- **{name}:** {_value(value)}"
            for name, value in hypothesis.items()
            if name not in _SKIPPED_FIELDS
        ]
        refs = hypothesis.get("evidence_refs")
        cited = (
            [cards[ref] for ref in refs if isinstance(ref, str) and ref in cards]
            if isinstance(refs, list)
            else []
        )
        if cited:
            lines += ["", "### Evidence", ""]
            for card in cited:
                lines += _evidence_lines(card)
        blocks.append("\n".join(lines).rstrip())
    if issues:
        blocks.append("\n".join(["## Known issues from hypothesis review", "", *issues]))
    return "\n\n".join(blocks) + "\n"


def _optional_body(ref, http: httpx.Client, label: str) -> dict | None:
    """An optional input never fails the run: any problem is logged and the input skipped."""
    try:
        response = http.get(ref.download_url)
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        if hashlib.sha256(response.content).hexdigest() != ref.sha256:
            raise ValueError("artifact hash mismatch")
        body = json.loads(response.content)
        if not isinstance(body, dict):
            raise ValueError("not a JSON object")
        return body
    except (httpx.HTTPError, ValueError) as exc:
        _LOGGER.warning("Skipping optional %s input: %s", label, exc)
        return None


def _evidence_cards(request: RunCreateRequest, http: httpx.Client) -> dict[str, dict]:
    cards: dict[str, dict] = {}
    for ref in request.inputs.artifacts:
        if ref.schema_id != I2H_EVIDENCE:
            continue
        body = _optional_body(ref, http, I2H_EVIDENCE)
        if body is None:
            continue
        try:
            found = {}
            for card in body["cards"]:
                if isinstance(card["card_id"], str) and isinstance(card["title"], str):
                    found[card["card_id"]] = card
            cards.update(found)
        except (KeyError, TypeError) as exc:
            _LOGGER.warning("Skipping malformed %s input: %r", I2H_EVIDENCE, exc)
    return cards


def _known_issues(
    request: RunCreateRequest, http: httpx.Client, ids: set[str], artifact_id: str
) -> list[str]:
    refs = [ref for ref in request.inputs.artifacts if ref.schema_id == REVIEW_FINDINGS]
    body = _optional_body(refs[-1], http, REVIEW_FINDINGS) if refs else None
    if body is None:
        return []
    issues = []
    try:
        for finding in body["findings"]:
            if finding["severity"] not in ("blocking", "major"):
                continue
            anchored = finding["anchor"].get("artifact_id")
            if isinstance(anchored, str) and anchored != artifact_id:
                continue
            locator = finding["anchor"]["locator"]
            match = _HYPOTHESIS_ANCHOR.match(locator)
            if match is None or any(match[1] == i or match[1].startswith(i + ".") for i in ids):
                issues.append(
                    f"- [{_trim(finding['finding_id'])}] ({finding['severity']}) "
                    f"{_trim(locator)}: {_trim(finding['issue'])}"
                )
    except (KeyError, TypeError, AttributeError) as exc:
        _LOGGER.warning("Skipping malformed %s input: %r", REVIEW_FINDINGS, exc)
        return []
    return issues


def _brief(request: RunCreateRequest) -> str:
    guidance = request.inputs.params.get("guidance", [])
    if not isinstance(guidance, list) or not all(isinstance(item, str) for item in guidance):
        raise InputError("params.guidance must be a list of strings")
    decisions = request.inputs.decisions
    lines = ([decisions.note] if decisions and decisions.note else []) + guidance
    if not lines:
        return ""
    return "\n## Research lead brief\n\n" + "".join(f"- {line}\n" for line in lines)


def fetch_inputs(request: RunCreateRequest, http: httpx.Client, workdir: Path) -> list[Path]:
    """``http`` already sends the X-Service-Key header; download URLs are opaque."""
    refs = [ref for ref in request.inputs.artifacts if ref.schema_id == I2H_HYPOTHESES]
    if len(refs) != 1:
        raise InputError("expected one i2h.hypotheses/v1 input")
    ref = refs[0]
    try:
        response = http.get(ref.download_url)
    except httpx.HTTPError as exc:
        raise InputError("input download failed") from exc
    if response.status_code != 200:
        raise InputError("input download failed")
    if hashlib.sha256(response.content).hexdigest() != ref.sha256:
        raise InputError("artifact hash mismatch", SHA256_MISMATCH)
    try:
        hypotheses = json.loads(response.content)["hypotheses"]
        ids = [h["id"] for h in hypotheses]
        for hypothesis in hypotheses:
            hypothesis["statement"]
    except (ValueError, KeyError, TypeError) as exc:
        raise InputError("malformed i2h.hypotheses/v1 input") from exc
    if not hypotheses or len(set(ids)) != len(ids):
        raise InputError("malformed i2h.hypotheses/v1 input")
    decisions = request.inputs.decisions
    chosen = (decisions.selection.get(SELECTION_HYPOTHESIS_IDS) if decisions else None) or ids
    if not set(chosen) <= set(ids):
        raise InputError("unknown hypothesis id")
    kept = [h for h in hypotheses if h["id"] in set(chosen)]
    workdir.mkdir(parents=True, exist_ok=True)
    brief = _brief(request)
    cards = _evidence_cards(request, http)
    issues = _known_issues(request, http, {h["id"] for h in kept}, ref.artifact_id)
    path = workdir / "hypotheses.md"
    path.write_text(_render(kept, cards, issues) + brief, encoding="utf-8")
    return [path]
