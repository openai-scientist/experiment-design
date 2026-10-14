import pytest

from experiment_design.api.contract_models import Phase
from experiment_design.api.mapping import failure_of, phase_of, step_key
from experiment_design.contracts import RunState


@pytest.mark.parametrize(
    ("stage", "status", "role", "phase"),
    [
        (2, "RUNNING", None, Phase.drafting),
        (2, "AWAITING_RESEARCHER", None, Phase.drafting),
        (4, "RUNNING", "methodologist", Phase.reviewing),
        (4, "RUNNING", "revision", Phase.revising),
        (4, "REQUESTED_CHANGES", None, Phase.revising),
        (6, "PENDING_REVIEW", None, Phase.awaiting_researcher),
        (7, "PUBLISHED", None, Phase.published),
        (3, "USAGE_UNKNOWN", None, Phase.failed),
        (5, "INCOMPLETE", None, Phase.awaiting_researcher),
        (4, "CANCELLED", None, Phase.cancelled),
        (4, "PAUSED", None, Phase.paused),
        (7, "RUNNING", None, Phase.awaiting_researcher),
        (3, "REJECTED", None, Phase.failed),
        (5, "STOPPED", None, Phase.failed),
    ],
)
def test_phase_of(stage, status, role, phase):
    assert phase_of(RunState(run_id="r", stage=stage, status=status), role=role) is phase


def test_step_keys_cover_seven_stages():
    assert [step_key(n) for n in range(1, 8)][0] == "s1_snapshot"
    assert step_key(7) == "s7_publish"
    assert len({step_key(n) for n in range(1, 8)}) == 7


def test_failure_codes():
    assert failure_of(RunState(run_id="r", status="REJECTED")).code == "rejected"
    assert failure_of(RunState(run_id="r", status="USAGE_UNKNOWN")).code == "usage_unknown"
    assert failure_of(RunState(run_id="r", status="STOPPED")).code == "workflow_stopped"
    assert failure_of(RunState(run_id="r", status="RUNNING")) is None
    assert failure_of(RunState(run_id="r", status="STOPPED"), reason="why").message == "why"
