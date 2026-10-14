"""Turn the contract inputs of a run into the Markdown files the workflow reads."""

import hashlib
import json
from pathlib import Path

import httpx

from experiment_design.api.contract_models import (
    I2H_HYPOTHESES,
    SELECTION_HYPOTHESIS_IDS,
    SHA256_MISMATCH,
    VALIDATION_ERROR,
    RunCreateRequest,
)

_SKIPPED_FIELDS = {"id", "statement"}


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


def _render(hypotheses: list[dict]) -> str:
    blocks = ["# Hypotheses"]
    for hypothesis in hypotheses:
        lines = [f"## {hypothesis['id']}: {hypothesis['statement']}", ""]
        lines += [
            f"- **{name}:** {_value(value)}"
            for name, value in hypothesis.items()
            if name not in _SKIPPED_FIELDS
        ]
        blocks.append("\n".join(lines).rstrip())
    return "\n\n".join(blocks) + "\n"


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
    path = workdir / "hypotheses.md"
    path.write_text(_render(kept), encoding="utf-8")
    return [path]
