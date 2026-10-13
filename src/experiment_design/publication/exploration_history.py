"""Render and export only frozen exploration proposals."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from experiment_design.design.exploration import ExplorationSnapshot
from experiment_design.persistence.store import Store


def render_exploration(snapshot: ExplorationSnapshot) -> str:
    body = snapshot.submission
    lines = [
        "# Experiment exploration",
        "",
        "Proposals for the next research step; execution approval has not been granted.",
        "",
        f"Snapshot: {snapshot.id}",
        f"Recommendation: {snapshot.selection.choice}",
        f"Outcome: {snapshot.outcome}",
        "",
        f"Intent interpretation (provisional): {body.intent_interpretation}",
        f"Intent sources: {', '.join(body.intent_source_refs)}",
        "",
        body.rationale,
        "",
        "## Priorities",
        "",
    ]
    for index, group in enumerate(body.priorities, 1):
        lines += [
            f"{index}. {', '.join(group.candidate_ids)}: {group.rationale}",
            f"   Conditions: {'; '.join(group.conditions) or 'none stated'}",
            f"   Evidence refs: {', '.join(group.evidence_refs) or 'methodological proposal'}",
        ]
    for candidate in body.candidates:
        lines += [
            "",
            f"## {candidate.id}: {candidate.question}",
            "",
            f"Candidate ref: {snapshot.id}#{candidate.id}",
            f"Purpose: {candidate.purpose}",
            f"Target uncertainty: {candidate.target_uncertainty}",
            "Alternative explanations (proposed): "
            + ("; ".join(candidate.alternative_explanations) or "none stated"),
            f"Method: {candidate.method_sketch}",
            f"Feasibility: {candidate.feasibility} — {candidate.feasibility_basis}",
            f"Conditions: {'; '.join(candidate.conditions) or 'none stated'}",
            f"Requirements: {'; '.join(candidate.resources.requirements) or 'none stated'}",
            f"Cost basis: {candidate.resources.cost_basis}",
            f"Resource unknowns: {'; '.join(candidate.resources.unknowns) or 'none stated'}",
            f"Assumptions: {'; '.join(candidate.assumptions) or 'none stated'}",
            f"Limitations: {'; '.join(candidate.limitations) or 'none stated'}",
            f"Evidence refs: {', '.join(candidate.evidence_refs) or 'methodological proposal'}",
            "",
            "Possible observations and next steps:",
            "",
        ]
        for outcome in candidate.outcomes:
            lines += [
                f"- {outcome.observation} → {outcome.next_step}",
                f"  Inference limit: {outcome.inference_limit}",
            ]
    lines += ["", "## Unknowns and focused questions", ""]
    lines += [f"- {item}" for item in body.unknowns + body.focused_questions]
    if body.defer_reason:
        lines += ["", f"Deferral reason: {body.defer_reason}"]
    if snapshot.observations:
        lines += ["", "## Accepted task observations", ""]
        lines += [
            f"- {observation.evidence_ref}: {observation.tool_name}; "
            "exact arguments, result and binding are included in exploration.json."
            for observation in snapshot.observations
        ]
    lines += ["", "## Supplied sources", ""]
    lines += [
        f"- {source.ref}: {source.path} (SHA-256 {source.sha256})"
        for source in snapshot.source.artifacts
    ]
    if snapshot.source.intake_issues:
        lines += ["", "Intake limitations:", ""]
        lines += [f"- {issue}" for issue in snapshot.source.intake_issues]
    return "\n".join(lines) + "\n"


def _json_bytes(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def export_exploration(store: Store, run_id: str, destination: Path) -> dict[str, Path]:
    if not store.is_exploration_run(run_id):
        raise ValueError("exploration export is historical only; export the canonical plan")
    snapshot = store.exploration_snapshot(run_id)
    if snapshot is None:
        raise ValueError("exploration has no frozen snapshot to export")
    files = {
        "exploration.json": _json_bytes(snapshot.model_dump(mode="json")),
        "exploration.md": render_exploration(snapshot).encode("utf-8"),
    }
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    files["manifest.json"] = _json_bytes(
        {
            "schema_version": 1,
            "run_id": run_id,
            "snapshot_id": snapshot.id,
            "snapshot_sha256": digests["exploration.json"],
            "files": digests,
        }
    )
    destination = Path(destination)
    if destination.is_symlink():
        raise ValueError("existing export destination is a symlink")
    if destination.exists():
        if (
            not destination.is_dir()
            or {path.name for path in destination.iterdir()} != set(files)
            or any(
                not (destination / name).is_file()
                or (destination / name).is_symlink()
                or (destination / name).read_bytes() != data
                for name, data in files.items()
            )
        ):
            raise ValueError("existing exploration export is different or partial")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix=f".{destination.name}-", dir=destination.parent
        ) as temporary:
            staged = Path(temporary) / "bundle"
            staged.mkdir()
            for name, data in files.items():
                (staged / name).write_bytes(data)
            staged.rename(destination)
    return {
        "json": destination / "exploration.json",
        "markdown": destination / "exploration.md",
        "manifest": destination / "manifest.json",
    }
