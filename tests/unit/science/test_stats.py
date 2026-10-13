"""References: uv run --with statsmodels tests/fixtures/stats_reference.py.

The generator never imports production code.
"""

import hashlib
import json
from pathlib import Path

import pytest

from experiment_design.contracts import CalculationRequest
from experiment_design.science.stats import calculate

REFERENCE = json.loads(Path("tests/fixtures/stats_reference.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", REFERENCE["cases"], ids=lambda case: case["name"])
def test_independent_reference(case):
    request = CalculationRequest.model_validate(case["request"])
    original = request.model_dump_json()
    result = calculate(request)
    assert result.status == "computed", result.reasons
    for key, value in case.get("expected", {}).items():
        assert getattr(result, key) == pytest.approx(value, abs=case.get("tolerance", 1e-9))
    for key, value in case.get("normalized", {}).items():
        assert result.normalized_inputs[key] == pytest.approx(value, abs=1e-12)
    if "monte_carlo_power" in case:
        assert case["monte_carlo_power"] == pytest.approx(0.8, abs=0.02)
        assert any("normal" in assumption.casefold() for assumption in result.assumptions)
    assert request.model_dump_json() == original
    assert calculate(request).id == result.id


@pytest.mark.parametrize(
    "method",
    [
        "paired_t",
        "wilcoxon_signed_rank",
        "mcnemar",
        "holm",
        "a12",
        "cliffs_delta",
        "bootstrap_ci",
    ],
)
def test_missing_inputs_are_not_computed(method):
    result = calculate(CalculationRequest(calculator_id=method))
    assert result.status == "not_computed" and result.reasons


def test_reference_generator_identity():
    assert (
        REFERENCE["provenance"]["generator_sha256"]
        == hashlib.sha256(Path("tests/fixtures/stats_reference.py").read_bytes()).hexdigest()
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "calculator_id": "paired_t",
            "alpha": 0.05,
            "sidedness": "two_sided",
            "effect": {"value": 0.5, "provenance": "source"},
            "sd": {"value": 1, "provenance": "assumption"},
            "n_pairs": 20,
        },
        {
            "calculator_id": "mcnemar",
            "alpha": 0.05,
            "sidedness": "two_sided",
            "n_pairs": 20,
            "p_discordant_10": {"value": 0.8, "provenance": "assumption"},
            "p_discordant_01": {"value": 0.8, "provenance": "assumption"},
        },
        {"calculator_id": "a12", "a12": {"value": 1, "provenance": "assumption"}},
        {"calculator_id": "holm", "p_values": [0.1, 1.1]},
        {
            "calculator_id": "cliffs_delta",
            "cliffs_delta": {"value": 0.12, "unit": "points", "provenance": "assumption"},
        },
    ],
)
def test_invalid_provenance_domains_and_units_do_not_compute(payload):
    result = calculate(CalculationRequest.model_validate(payload))
    assert result.status == "not_computed" and result.reasons


def test_derived_overflow_is_not_a_computed_result():
    result = calculate(
        CalculationRequest(
            calculator_id="paired_t",
            alpha=0.05,
            sidedness="two_sided",
            target_power=0.8,
            effect={"value": 1e308, "provenance": "assumption"},
            sd={"value": 1e-308, "provenance": "assumption"},
        )
    )
    assert result.status == "not_computed" and result.reasons


@pytest.mark.parametrize(
    "payload",
    [
        {
            "calculator_id": "paired_t",
            "alpha": 0.05,
            "sidedness": "two_sided",
            "n_pairs": 20,
            "effect": {"value": 1e308, "provenance": "assumption"},
            "sd": {"value": 1, "provenance": "assumption"},
        },
        {
            "calculator_id": "bootstrap_ci",
            "values": [1e308, 1e308],
            "confidence": 0.95,
            "resamples": 20,
            "random_seed": 1,
        },
    ],
)
def test_nonfinite_scipy_output_is_not_computed(payload):
    result = calculate(CalculationRequest.model_validate(payload))
    assert result.status == "not_computed" and result.reasons
