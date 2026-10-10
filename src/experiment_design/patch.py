"""Validated, immutable design changes and researcher claim ownership."""

import hashlib
import json
import re
from typing import Any

from pydantic import ValidationError

from experiment_design.contracts import (
    CLAIM_FIELDS,
    ClaimField,
    Design,
    Origin,
    Patch,
    PatchOp,
    ScientificPackage,
)

RESEARCHER_ORIGINS = {"package", "researcher"}


class PatchError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def package_claim(package: ScientificPackage) -> dict[str, str | float | None]:
    h, o, i = package.hypothesis, package.outcome, package.inference
    return {
        "hypothesis": h.statement,
        "prediction": h.prediction,
        "failure_condition": h.failure_condition,
        "exposure": h.exposure,
        "inference_scope": package.scope,
        "primary_outcome": o.name if o else None,
        "outcome_unit": o.unit if o else None,
        "outcome_kind": o.kind if o else None,
        "direction": o.direction if o else None,
        "contrast_type": i.contrast_type if i else None,
        "margin": i.margin if i else None,
        "estimand": i.estimand if i else None,
    }


def initial_design(package: ScientificPackage, proposed: Design) -> Design:
    result = proposed.model_copy(deep=True)
    errors = []
    for name, known in package_claim(package).items():
        field = getattr(result.claim, name)
        if known is not None:
            if field.value is not None and field.value != known:
                errors.append(f"{name} is researcher-owned; proposed value differs from package")
            setattr(result.claim, name, ClaimField(value=known, origin="package"))
        else:
            field.origin = "designer_default" if field.value is not None else "missing"
    if errors:
        raise PatchError(errors)
    return result


def quantities(value: Any, path: str = ""):
    """Yield JSON-pointer paths and quantity records from scientific content."""
    if isinstance(value, dict):
        if "provenance" in value and "value" in value:
            yield path, value
        for key, child in value.items():
            yield from quantities(child, path + "/" + key.replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from quantities(child, path + "/" + str(index))


def _parts(path: str) -> list[str]:
    if path in {"", "/"}:
        return []
    if not path.startswith("/") or re.search(r"~(?![01])", path):
        raise PatchError([f"Bad pointer: {path}"])
    return [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]


def _read(data: Any, parts: list[str], path: str) -> Any:
    try:
        for key in parts:
            if isinstance(data, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", key):
                    raise ValueError(key)
                data = data[int(key)]
            else:
                data = data[key]
        return data
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise PatchError([f"Bad pointer: {path}"]) from exc


def resolve(design: Design, path: str) -> Any:
    return _read(design.model_dump(mode="json"), _parts(path), path)


def apply_patch(
    design: Design,
    patch: Patch,
    *,
    source_ids: set[str],
    author: str = "agent",
) -> Design:
    data = design.model_dump(mode="json")
    errors = []
    for op in patch.ops:
        try:
            parts = _parts(op.path)
            if len(parts) >= 2 and parts[0] == "claim":
                name = parts[1]
                if name not in CLAIM_FIELDS:
                    raise PatchError([f"Bad pointer: {op.path}"])
                if len(parts) >= 3 and parts[2] == "origin":
                    raise PatchError([f"Claim origin cannot be edited: {op.path}"])
                if len(parts) == 2:
                    parts.append("value")
            if not parts:
                data = op.value
            else:
                parent = _read(data, parts[:-1], op.path)
                _read(parent, [parts[-1]], op.path)
                key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
                parent[key] = op.value
        except PatchError as exc:
            errors.extend(exc.errors)
    try:
        result = Design.model_validate(data)
    except ValidationError as exc:
        raise PatchError(errors + [str(exc)]) from exc
    for name in CLAIM_FIELDS:
        before, after = getattr(design.claim, name), getattr(result.claim, name)
        if author != "researcher" and before.origin in RESEARCHER_ORIGINS and after != before:
            errors.append(f"{name} is researcher-owned")
        if after.origin != before.origin:
            errors.append(f"Claim origin cannot be edited: {name}")
        if after.value != before.value:
            after.origin = "researcher" if author == "researcher" else "designer_default"
    for path, quantity in quantities(result.model_dump(mode="json")):
        if quantity["provenance"] == "source" and quantity["source_ref"] not in source_ids:
            errors.append(f"{path}: unknown source_ref {quantity['source_ref']}")
    if errors:
        raise PatchError(errors)
    return result


def set_claim(design: Design, field: str, value: str | float, origin: Origin) -> Design:
    if field not in CLAIM_FIELDS or origin != "researcher":
        raise PatchError(["Only researcher answers may set a known claim field"])
    source_ids = {
        q["source_ref"]
        for _, q in quantities(design.model_dump(mode="json"))
        if q["provenance"] == "source"
    }
    result = apply_patch(
        design,
        Patch(ops=[PatchOp(path=f"/claim/{field}", value=value)], rationale="Researcher answer"),
        source_ids=source_ids,
        author="researcher",
    )
    getattr(result.claim, field).origin = origin
    return result


def design_diff(before: Design, after: Design) -> list[dict]:
    changes = []

    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(a.keys() | b.keys()):
                walk(a.get(key), b.get(key), path + "/" + key)
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for i, (left, right) in enumerate(zip(a, b, strict=True)):
                walk(left, right, path + "/" + str(i))
        elif a != b:
            changes.append({"path": path or "/", "before": a, "after": b})

    walk(before.model_dump(mode="json"), after.model_dump(mode="json"), "")
    return changes


def design_hash(design: Design) -> str:
    content = json.dumps(design.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
