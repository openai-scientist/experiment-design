"""Pure mapping from exd run state to contract phase, step, failure and gate."""

from collections.abc import Sequence
from datetime import UTC, datetime

from experiment_design.api.contract_models import (
    FailureInfo,
    Gate,
    GateKind,
    InteractionMode,
    Phase,
)
from experiment_design.contracts import RunState
from experiment_design.persistence.store import RunStore
from experiment_design.vocabulary import STEPS

_STATUS_PHASE = {
    "PUBLISHED": Phase.published,
    "REJECTED": Phase.failed,
    "USAGE_UNKNOWN": Phase.failed,
    "STOPPED": Phase.failed,
    "FAILED": Phase.failed,
    "INCOMPLETE": Phase.awaiting_researcher,
    "CANCELLED": Phase.cancelled,
    "PAUSED": Phase.paused,
    "PENDING_REVIEW": Phase.awaiting_researcher,
    "REQUESTED_CHANGES": Phase.revising,
}
_FAILURES = {
    "REJECTED": ("rejected", "The researcher rejected the plan."),
    "USAGE_UNKNOWN": ("usage_unknown", "Provider token usage is unknown, so the run stopped."),
    "STOPPED": ("workflow_stopped", "The workflow stopped before a reviewed plan was ready."),
}


def step_key(stage: int) -> str:
    return STEPS[stage - 1][0]


def phase_of(state: RunState, *, role: str | None) -> Phase:
    if state.status in _STATUS_PHASE:
        return _STATUS_PHASE[state.status]
    if state.stage <= 3:
        return Phase.drafting
    if state.stage <= 5:
        return Phase.revising if role == "revision" else Phase.reviewing
    return Phase.awaiting_researcher


def failure_of(state: RunState, *, reason: str = "") -> FailureInfo | None:
    if state.status not in _FAILURES:
        return None
    code, message = _FAILURES[state.status]
    return FailureInfo(code=code, message=reason or message)


def open_gate(
    store: RunStore, *, artifact_ids: Sequence[str], mode: InteractionMode
) -> Gate | None:
    """The gate a held run waits behind; S1 knows only the researcher review of the plan."""
    state = store.state
    if state.status == "PENDING_REVIEW" and state.stage == 6:
        return Gate(
            gate_id=f"g{state.attempt}-s6-review",
            kind=GateKind.artifact_review,
            step_key=step_key(6),
            prompt="Review the experiment plan",
            artifact_ids=list(artifact_ids),
            opened_at=datetime.now(UTC),
        )
    return None
