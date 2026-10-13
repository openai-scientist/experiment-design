"""S1 captures immutable researcher inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from experiment_design.contracts import StageResult

if TYPE_CHECKING:
    from experiment_design.workflow.dependencies import Deps


def run(run_id: str, deps: Deps) -> StageResult:
    return StageResult(
        stage_id="S1",
        status="COMPLETE",
        binding=deps.store.binding(run_id),
        output_refs=[deps.store.source_snapshot(run_id).id],
        reason="Readable sources captured exactly",
    )
