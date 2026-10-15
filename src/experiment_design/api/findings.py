"""Translate current scientific findings with persistent engine-assigned identities."""

import json
from contextlib import suppress
from typing import Literal

from experiment_design.api.contract_models import (
    Anchor,
    FindingCategory,
    ReviewFindings,
    RevisionItem,
    RevisionResponse,
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
        kind=f.kind,
        issue=f.issue + " Consequence: " + f.consequence,
        suggestion=f.suggestion,
        evidence_refs=f.evidence_refs,
    )


CRITIC_REVIEWS = ("methodologist_review.json", "skeptic_review.json")


def mapped_findings(
    store: RunStore,
    stage: int,
    name: str,
    *,
    round: int,
    artifact_id: str,
    open_after_final: bool | None = None,
):
    result = []
    for record in store.bound_findings(stage, name, round):
        finding_id = record.pop("finding_id")
        finding = Finding.model_validate(record)
        result.append(
            to_contract_finding(
                finding,
                open_after_final=stage == 5 if open_after_final is None else open_after_final,
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
        if not store.valid(4):
            raise WorkflowError("Current final review is absent or invalid")
        # Stopped after the critics: their findings are what stays open (live: a stop before
        # the final check reported no findings, so the ledger and the published plan lost five).
        stopped = []
        for name in CRITIC_REVIEWS:
            with suppress(FileNotFoundError):
                stopped += mapped_findings(
                    store, 4, name, round=round, artifact_id=artifact_id, open_after_final=True
                )
        return ReviewFindings(
            round=round,
            reviewed_artifact_id=artifact_id,
            reviewer_model=reviewer_model,
            findings=stopped,
            passed=False,
        )
    final = mapped_findings(store, 5, "final_review.json", round=round, artifact_id=artifact_id)
    original = []
    for name in CRITIC_REVIEWS:
        with suppress(FileNotFoundError):
            original += mapped_findings(store, 4, name, round=round, artifact_id=artifact_id)
    return ReviewFindings(
        round=round,
        reviewed_artifact_id=artifact_id,
        reviewer_model=reviewer_model,
        findings=[*original, *final],
        passed=not any(f.severity == "blocking" and f.kind == "fix" for f in final),
    )


def revision_response(
    store: RunStore, *, round: int, before_id: str, after_id: str
) -> RevisionResponse:
    metadata = json.loads(store.read(4, "revised_plan.json"))
    return RevisionResponse(
        round=round,
        artifact_before=before_id,
        artifact_after=after_id,
        items=[RevisionItem.model_validate(r) for r in metadata.get("responses", [])],
    )
