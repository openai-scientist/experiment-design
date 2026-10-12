"""S7 publishes a frozen snapshot without recomputing scientific claims."""

from __future__ import annotations

from typing import TYPE_CHECKING

from experiment_design.contracts import PublicationSnapshot, StageResult
from experiment_design.runtime.store import Store

if TYPE_CHECKING:
    from experiment_design.workflow import Deps


def prepare_publication(
    store: Store, run_id: str, version: int | None = None
) -> PublicationSnapshot:
    current = store.publication_snapshot(run_id, version)
    for event in reversed(store.events(run_id)):
        if event.type != "publication" or event.data["manifest"].get("contract") != "exp-plan/v1":
            continue
        binding = event.data["binding"]
        if binding["design_version"] != current.version.version:
            continue
        published = store.publication_snapshot(run_id, event_id=binding["event_id"])
        # Stage progress/publication telemetry does not alter the scientific snapshot.
        if version is not None or published.model_dump(
            exclude={"run", "binding"}
        ) == current.model_dump(exclude={"run", "binding"}):
            return published
    return current


def run(run_id: str, deps: Deps) -> StageResult:
    return StageResult(
        stage_id="S7",
        status="NEEDS_INPUT",
        binding=deps.store.binding(run_id),
        reason="Publication needs explicit output destination",
    )
