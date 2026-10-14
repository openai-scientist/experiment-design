import json

import pytest

from experiment_design.api.findings import review_findings, to_contract_finding
from experiment_design.config import RunConfig
from experiment_design.contracts import Finding
from experiment_design.workflow.engine import advance
from tests.integration.review.test_markdown_workflow import MAJOR, happy_outputs, store_for
from tests.support.providers import Judge, Provider


@pytest.mark.parametrize(
    ("severity", "open_after_final", "expected"),
    [
        ("MAJOR", True, "blocking"),
        ("MAJOR", False, "major"),
        ("MINOR", True, "minor"),
    ],
)
def test_severity_anchor_and_category_mapping(severity, open_after_final, expected):
    finding = Finding(**(MAJOR | {"severity": severity, "category": "framing"}))
    mapped = to_contract_finding(
        finding,
        open_after_final=open_after_final,
        author="reviewer",
        artifact_id="draft",
        finding_id="F1-1",
    )
    assert mapped.severity == expected
    assert mapped.anchor.locator == "document:exp_plan.md"
    assert mapped.category == "other"
    assert mapped.finding_id == "F1-1"


def test_current_final_findings_and_stable_engine_ids(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[2]["findings"] = [MAJOR]
    outputs[4]["findings"] = [MAJOR]

    # No automatic revision: final route is INCOMPLETE, keeping current original reviews.
    class Check(Judge):
        def decide(self, state, questions):
            from experiment_design.runtime.jev import Decision

            options = questions["next"].options
            return {
                "next": Decision("CHECK" if "CHECK" in options else next(iter(options)), {}, 1, 1)
            }

    # Use a minor original which passes S4, then material at S5.
    outputs[2]["findings"] = [MAJOR | {"severity": "MINOR"}]
    advance(store, Provider(outputs), Check(), RunConfig(max_refinements=0))
    report = review_findings(store, round=1, artifact_id="draft", reviewer_model="review-m")
    assert report.passed is False
    assert any(f.severity == "blocking" for f in report.findings)
    assert all(f.finding_id.startswith("F1-") for f in report.findings)
    again = review_findings(store, round=1, artifact_id="draft", reviewer_model="review-m")
    assert again == report
    store.write(5, "final_review.json", json.dumps({"findings": []}))
    with pytest.raises(ValueError, match="current|valid|review"):
        review_findings(store, round=1, artifact_id="draft", reviewer_model="review-m")


def test_reviewer_schema_failure_opens_a_gate_with_error(tmp_path):
    from experiment_design.api.contract_models import ModelRole, Phase
    from experiment_design.runtime.llm import LLMResponse, ToolCall
    from tests.integration.api.test_run_manager import _manager, _wait
    from tests.support.api_fakes import EndlessProvider, make_request

    class InvalidReview(EndlessProvider):
        def complete(self, messages, tools):
            calls = self.reading_calls(messages)
            if calls:
                return LLMResponse(content=None, tool_calls=calls, tokens_in=2, tokens_out=3)
            return LLMResponse(
                content=None,
                tokens_in=2,
                tokens_out=3,
                tool_calls=[ToolCall(id="bad", name="submit", arguments="{}")],
            )

    manager, platform, _ = _manager(
        tmp_path,
        llm_factory=lambda cfg, key, role, call: (
            InvalidReview() if role is ModelRole.review else EndlessProvider()
        ),
    )
    run_id = manager.create(make_request(platform, stage_run_id="error")).engine_run_id
    state = _wait(manager, run_id, Phase.awaiting_researcher, gate=True)
    assert state.open_gate.kind == "artifact_review"
    assert manager.wait_idle(run_id) and manager.flush(run_id)
    event = next(e for e in platform.events["error"] if e.type == "review.completed")
    assert event.payload["passed"] is False and event.payload["error"]
    assert event.payload["artifact_id"] is None
    assert not manager._runs[run_id].store.state.usage_unknown
