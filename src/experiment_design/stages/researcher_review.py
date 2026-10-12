"""S6 presents the exact reviewed plan; only recorded replies confer authority."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from experiment_design.contracts import ResearcherDecision, StageResult

if TYPE_CHECKING:
    from experiment_design.workflow import Deps


def run(run_id: str, deps: Deps) -> StageResult:
    store = deps.store
    finals = [
        d
        for d in store.decisions(run_id)
        if d.purpose == "final_review"
        and d.status == "AWAITING_RESEARCHER"
        and d.binding.model_dump(exclude={"event_id"})
        == store.binding(run_id).model_dump(exclude={"event_id"})
        and d.dependencies == [store.assessment(run_id).id]
    ]
    if not finals:
        finals = [
            store.save_decision(
                ResearcherDecision(
                    id="final:" + uuid4().hex,
                    run_id=run_id,
                    purpose="final_review",
                    status="AWAITING_RESEARCHER",
                    binding=store.binding(run_id),
                    problem="Review this exact design and its scientific plan assessment",
                    dependencies=[store.assessment(run_id).id],
                )
            )
        ]
    outputs, status, reason = (
        [finals[-1].id],
        "NEEDS_INPUT",
        "Waiting for final researcher review",
    )
    return StageResult(
        stage_id="S6",
        status=status,
        binding=store.binding(run_id),
        output_refs=outputs,
        reason=reason,
    )
