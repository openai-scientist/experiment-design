"""Translate current scientific findings with persistent engine-assigned identities."""

from contextlib import suppress
from typing import Literal

from experiment_design.api.contract_models import (
    Anchor,
    FindingCategory,
    ReviewFindings,
)
from experiment_design.api.contract_models import (
    Finding as WireFinding,
)
from experiment_design.contracts import Finding, WorkflowError
from experiment_design.persistence.store import RunStore


def to_contract_finding(
    f: Finding,
    *,
    open_after_final: bool,
    author: Literal["reviewer", "researcher"],
    artifact_id: str | None,
    finding_id: str,
) -> WireFinding:
    return WireFinding(
        finding_id=finding_id,
        author=author,
        anchor=Anchor(
            artifact_id=artifact_id,
            locator=f"section:{f.anchor}" if f.anchor else "document:exp_plan.md",
        ),
        severity=("blocking" if open_after_final else "major")
        if f.severity == "MAJOR"
        else "minor",
        category=f.category if f.category in FindingCategory else "other",
        issue=f.issue + " Consequence: " + f.consequence,
        suggestion=f.suggestion,
        evidence_refs=f.evidence_refs,
    )


def mapped_findings(store: RunStore, stage: int, name: str, *, round: int, artifact_id: str):
    result = []
    for record in store.bound_findings(stage, name, round):
        finding_id = record.pop("finding_id")
        finding = Finding.model_validate(record)
        result.append(
            to_contract_finding(
                finding,
                open_after_final=stage == 5,
                author="reviewer",
                artifact_id=artifact_id,
                finding_id=finding_id,
            )
        )
    return result


def review_findings(
    store: RunStore,
    *,
    round: int,
    artifact_id: str,
    reviewer_model: str,
) -> ReviewFindings:
    if not store.valid(5):
        raise WorkflowError("Current final review is absent or invalid")
    final = mapped_findings(store, 5, "final_review.json", round=round, artifact_id=artifact_id)
    original = []
    for name in ("methodologist_review.json", "skeptic_review.json"):
        with suppress(FileNotFoundError):
            original += mapped_findings(store, 4, name, round=round, artifact_id=artifact_id)
    return ReviewFindings(
        round=round,
        reviewed_artifact_id=artifact_id,
        reviewer_model=reviewer_model,
        findings=[*original, *final],
        passed=not any(f.severity == "blocking" for f in final),
    )
