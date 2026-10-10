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
    Patch,
    Record,
    ResearchContext,
    ResearcherDecision,
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


def initial_design(
    package: ScientificPackage, proposed: Design, context: ResearchContext | None = None
) -> Design:
    result = proposed.model_copy(deep=True)
    errors = []
    for name, known in package_claim(package).items():
        field = getattr(result.claim, name)
        if known is not None:
            if field.value is not None and field.value != known:
                errors.append(f"{name} is researcher-owned; proposed value differs from package")
            annotation = context.field_annotations.get(name) if context else None
            origin = (
                "designer_default"
                if context is not None
                and (annotation is None or annotation.status in {"agent-proposed", "unknown"})
                else "package"
            )
            if annotation and annotation.status == "researcher-confirmed":
                origin = "researcher"
            setattr(result.claim, name, ClaimField(value=known, origin=origin))
        else:
            field.origin = "designer_default" if field.value is not None else "missing"
    if context:
        for path, value in scientific_commitments(context).items():
            if resolve(result, path) != value:
                errors.append(f"{path} is researcher-owned; proposed value differs from source")
    if errors:
        raise PatchError(errors)
    return result


def scientific_commitments(context: ResearchContext) -> dict[str, Any]:
    """Resolve source/confirmed commitments and their required measurement mirrors."""
    values = {}
    for name, path in (
        ("primary_outcome", "/measurement/primary_metric/name"),
        ("outcome_unit", "/measurement/primary_metric/unit"),
        ("outcome_kind", "/measurement/primary_metric/kind"),
    ):
        annotation = context.field_annotations.get(name)
        if annotation and annotation.status in {"source-stated", "researcher-confirmed"}:
            values[path] = package_claim(context.package)[name]
    for path, annotation in context.field_annotations.items():
        if path.startswith("/context/"):
            continue
        if not path.startswith("/") or annotation.status not in {
            "source-stated",
            "researcher-confirmed",
        }:
            continue
        value = annotation.value
        if value is None and path == "/analysis/model":
            value = context.package.hypothesis.method
        if value is None and path == "/analysis/alpha":
            numbers = re.findall(r"[-+]?(?:\d*\.\d+|\d+)", annotation.source_span or "")
            if len(numbers) == 1:
                value = float(numbers[0])
        if value is None:
            raise PatchError([f"{path} is researcher-owned; typed commitment value is missing"])
        values[path] = value
    return values


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


def resolve(design: Record, path: str) -> Any:
    return _read(design.model_dump(mode="json"), _parts(path), path)


def apply_patch(
    design: Design,
    patch: Patch,
    *,
    source_ids: set[str],
    author: str = "agent",
    context: ResearchContext | None = None,
    authorization: ResearcherDecision | None = None,
) -> Design:
    if author == "researcher":
        if (
            authorization is None
            or authorization.status != "ANSWERED"
            or not authorization.responses
        ):
            raise PatchError(["Researcher authorization is required"])
        response = authorization.responses[-1]
        if (
            response.action not in {"SELECT_OPTION", "PROPOSE_ALTERNATIVE"}
            or (
                response.revision != authorization.revision
                and not (
                    authorization.reconciliation.get("independent") is True
                    and authorization.reconciliation.get("response_id") == response.response_id
                    and authorization.reconciliation.get("response_revision") == response.revision
                )
            )
            or authorization.binding.design_hash != design_hash(design)
            or not authorization.interpretation.get("authorized")
            or authorization.application
        ):
            raise PatchError(["Researcher authorization is stale or inapplicable"])
        if any(
            not any(
                op.path == path or op.path.startswith(path + "/")
                for path in authorization.affected_paths
            )
            for op in patch.ops
        ):
            raise PatchError(["Patch exceeds researcher authorization scope"])
    result = _apply_patch(design, patch, source_ids=source_ids, author=author)
    if context is not None and author != "researcher":
        impact = scientific_impact(design, result, context)
        if impact:
            raise PatchError([f"{path} is researcher-owned" for path in impact])
    return result


def scientific_impact(before: Design, after: Design, context: ResearchContext) -> list[str]:
    owned = scientific_commitments(context)
    return [
        change["path"]
        for change in design_diff(before, after)
        if any(
            change["path"] == path
            or change["path"].startswith(path + "/")
            or path.startswith(change["path"] + "/")
            for path, value in owned.items()
            if resolve(after, path) != value
        )
    ]


def preview_design(
    design: Design, patch: Patch, *, context: ResearchContext, source_ids: set[str]
) -> Design:
    result = _apply_patch(design, patch, source_ids=source_ids, author="preview")
    for name in CLAIM_FIELDS:
        if getattr(result.claim, name).value != getattr(design.claim, name).value:
            getattr(result.claim, name).origin = "designer_default"
    return result


def _apply_patch(design: Design, patch: Patch, *, source_ids: set[str], author: str) -> Design:
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
        if (
            author not in {"researcher", "preview"}
            and before.origin in RESEARCHER_ORIGINS
            and after != before
        ):
            errors.append(f"{name} is researcher-owned")
        if after.origin != before.origin:
            errors.append(f"Claim origin cannot be edited: {name}")
        if after.value != before.value:
            after.origin = "researcher" if author == "researcher" else "designer_default"
    for path, quantity in quantities(result.model_dump(mode="json")):
        if quantity["provenance"] == "computed":
            errors.append(f"{path}: unattested computed quantity")
        if quantity["provenance"] == "source" and quantity["source_ref"] not in source_ids:
            errors.append(f"{path}: unknown source_ref {quantity['source_ref']}")
    if errors:
        raise PatchError(errors)
    return result


def record_diff(before: Record, after: Record) -> list[dict]:
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


def design_diff(before: Design, after: Design) -> list[dict]:
    return record_diff(before, after)


def design_hash(design: Design) -> str:
    content = json.dumps(design.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
