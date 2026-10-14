import json

from experiment_design.api.contract_models import Phase, RevisionDiff, RevisionResponse
from experiment_design.runtime.llm import LLMResponse, ToolCall
from tests.integration.api.test_run_manager import _manager, _wait
from tests.integration.review.test_markdown_workflow import MAJOR
from tests.support.api_fakes import EndlessProvider, make_request


class Critical(EndlessProvider):
    def complete(self, messages, tools):
        task = json.loads(messages[1]["content"])
        if "input" in task or self.reading_calls(messages):
            return super().complete(messages, tools)
        self.answers += 1
        body = {"markdown": f"Review {self.answers}"}
        if task["task"].startswith("Role: methodologist.") and self.answers == 1:
            body["findings"] = [MAJOR]
        return LLMResponse(
            content=None,
            tokens_in=2,
            tokens_out=3,
            tool_calls=[ToolCall(id="s", name="submit", arguments=json.dumps(body))],
        )


def test_revision_diff_response_and_plan_bindings_are_delivered(tmp_path):
    from experiment_design.api.contract_models import ModelRole

    manager, platform, _ = _manager(
        tmp_path,
        llm_factory=lambda cfg, key, role, call: (
            Critical() if role is ModelRole.review else EndlessProvider()
        ),
    )
    run_id = manager.create(make_request(platform, stage_run_id="revision")).engine_run_id
    _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    assert manager.wait_idle(run_id) and manager.flush(run_id)
    artifacts = platform.artifacts["revision"]
    diff_artifact = next(a for a in artifacts if a.meta.schema_id == "revision.diff/v1")
    response_artifact = next(a for a in artifacts if a.meta.schema_id == "revision.response/v1")
    diff = RevisionDiff.model_validate_json(diff_artifact.content)
    response = RevisionResponse.model_validate_json(response_artifact.content)
    assert diff.patch.startswith("--- previous_plan")
    assert diff.round == response.round == 1
    assert (diff.artifact_before, diff.artifact_after) == (
        response.artifact_before,
        response.artifact_after,
    )
    ids = {a.artifact_id for a in artifacts}
    assert {diff.artifact_before, diff.artifact_after} <= ids
    assert response.items and response.items[0].finding_id.startswith("F1-")
    assert response.items[0].outcome == "not_fixed"
