"""S7 publishes a frozen snapshot without recomputing scientific claims."""

from __future__ import annotations

from typing import TYPE_CHECKING

from experiment_design.contracts import StageResult

if TYPE_CHECKING:
    from experiment_design.workflow.dependencies import Deps


def run(run_id: str, deps: Deps) -> StageResult:
    return StageResult(
        stage_id="S7",
        status="NEEDS_INPUT",
        binding=deps.store.binding(run_id),
        reason="Publication needs explicit output destination",
    )
