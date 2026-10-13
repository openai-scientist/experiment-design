"""Calculator activation must preserve legacy CLI behavior and supplied-source guards."""

import pytest

from experiment_design.agents.tools import run_specialist
from experiment_design.config import RunConfig
from experiment_design.contracts import CalculationRequest, Profile
from experiment_design.packs import load_pack
from experiment_design.science.registry import calculate
from tests.integration.review.test_markdown_workflow import store_for
from tests.support.providers import ToolProvider
from tests.unit.science.test_calculations import request as legacy_request


def test_dispatches_enabled_calculator():
    result = calculate(
        CalculationRequest(
            calculator_id="a12",
            a12={
                "value": 0.5,
                "provenance": "assumption",
            },
        ),
        frozenset({"a12"}),
    )
    assert result.status == "computed" and result.normalized_inputs["d"] == 0


def test_disabled_calculator_returns_no_computation():
    result = calculate(legacy_request(), frozenset({"a12"}))
    assert result.status == "not_computed"
    assert result.power is None and result.required_n_per_arm is None
    assert result.reasons[0].startswith("calculator not enabled for profile")


def invoke(store, payload, pack=None):
    provider = ToolProvider([("calculate", payload), ("submit", {"markdown": "Checked."})])
    run_specialist("design", store, provider, RunConfig(), {}, [], pack=pack)
    return [
        event["data"]["result"]
        for event in store.events()
        if event["kind"] == "tool.result" and event["data"]["tool"] == "calculate"
    ][0]


def test_no_pack_keeps_legacy_calculator_enabled(tmp_path):
    result = invoke(store_for(tmp_path), legacy_request().model_dump(mode="json"))
    assert result["status"] == "computed" and result["required_n_per_arm"] > 0


def test_no_pack_does_not_enable_overlay_calculators(tmp_path):
    result = invoke(
        store_for(tmp_path),
        {
            "calculator_id": "a12",
            "a12": {"value": 0.5, "provenance": "assumption"},
        },
    )
    assert result["status"] == "not_computed"


def test_active_pack_enables_new_calculator(tmp_path):
    result = invoke(
        store_for(tmp_path),
        {
            "calculator_id": "a12",
            "a12": {"value": 0.5, "provenance": "assumption"},
        },
        load_pack(Profile(research_type="comparative_benchmark")),
    )
    assert result["status"] == "computed" and result["normalized_inputs"]["d"] == 0


@pytest.mark.parametrize("field", ["a12", "cliffs_delta", "p_discordant_10", "p_discordant_01"])
def test_new_quantities_cannot_claim_an_invented_source_span(tmp_path, field):
    payload = {"calculator_id": "a12" if field == "a12" else "cliffs_delta"}
    if field.startswith("p_discordant"):
        payload = {
            "calculator_id": "mcnemar",
            "alpha": 0.05,
            "sidedness": "two_sided",
            "n_pairs": 20,
            "p_discordant_10": {"value": 0.2, "provenance": "assumption"},
            "p_discordant_01": {"value": 0.1, "provenance": "assumption"},
        }
    payload[field] = {
        "value": 0.56,
        "provenance": "source",
        "source_ref": "stage-01/hypothesis.md",
        "source_span": "Invented 0.56",
    }
    result = invoke(
        store_for(tmp_path), payload, load_pack(Profile(research_type="comparative_benchmark"))
    )
    assert result["accepted"] is False and "source" in result["error"]
