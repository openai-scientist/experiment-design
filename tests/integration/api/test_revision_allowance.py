import pytest

from experiment_design.api.contract_models import GateAnswer, Phase
from experiment_design.api.runs import RequestRejected
from tests.integration.api.test_run_manager import FINDING, _manager, _wait
from tests.support.api_fakes import make_request


def test_researcher_comment_has_its_own_revision_allowance(tmp_path):
    manager, platform, _ = _manager(tmp_path)
    request = make_request(platform, stage_run_id="allowance")
    request.review_policy.max_auto_revisions = 0
    run_id = manager.create(request).engine_run_id
    first = _wait(manager, run_id, Phase.awaiting_researcher, gate=True).open_gate
    manager.answer(
        run_id,
        first.gate_id,
        GateAnswer(action="comment", actor="user", decided_by="r", findings=[FINDING]),
    )
    second = _wait(manager, run_id, Phase.awaiting_researcher, gate=True).open_gate
    assert second.gate_id != first.gate_id
    store = manager._runs[run_id].store
    assert store.state.refinements == 1
    assert store.config()["run"]["max_refinements"] == 1
    assert any(e["kind"] == "limits.changed" for e in store.events())


def test_insufficient_token_reserve_preserves_gate_and_allowance(tmp_path):
    manager, platform, _ = _manager(tmp_path)
    run_id = manager.create(make_request(platform, stage_run_id="allowance")).engine_run_id
    gate = _wait(manager, run_id, Phase.awaiting_researcher, gate=True).open_gate
    assert manager.wait_idle(run_id)
    run = manager._runs[run_id]
    run.store.update(llm_tokens=run.store.state.llm_token_limit - 1)
    before = run.cfg.run.max_refinements
    with pytest.raises(RequestRejected, match="token_allowance_exhausted"):
        manager.answer(
            run_id, gate.gate_id, GateAnswer(action="comment", actor="user", findings=[FINDING])
        )
    assert manager.state(run_id).open_gate.gate_id == gate.gate_id
    assert run.cfg.run.max_refinements == before
    assert not any(e["kind"] == "researcher.response" for e in run.store.events())
