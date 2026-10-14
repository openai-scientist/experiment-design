"""Explicit ML/SE planning calculations; assumptions are part of every result.

Paired power uses the noncentral t distribution. Wilcoxon planning uses its
normal-shift asymptotic relative efficiency, not exact distribution-free power.
McNemar uses Connor's multinomial normal approximation, without continuity
correction. A12-to-d assumes equal-variance normal location shifts; Cliff's
delta-to-A12 is algebraic. None of these assess the scientific plan's validity.
"""

import hashlib
import json
import math
from collections.abc import Callable

import numpy as np
from scipy.stats import bootstrap, nct, norm, t

from experiment_design.contracts import CalculationRequest, CalculationResult, Quantity
from experiment_design.science.power import normalize


class _NotComputed(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _NotComputed(reason)


def _quantity(result: CalculationResult, name: str, quantity: Quantity | None) -> float:
    _require(quantity is not None and quantity.value is not None, f"{name} is missing")
    _require(quantity.provenance in {"source", "assumption"}, f"{name} provenance is invalid")
    _require(
        quantity.provenance != "source" or bool(quantity.source_ref), f"{name} source ref missing"
    )
    result.input_provenance[name] = quantity.provenance
    if quantity.provenance == "assumption":
        result.assumptions.append(f"{name} is an explicit assumption.")
    return quantity.value


def _probability(result: CalculationResult, name: str, quantity: Quantity | None) -> float:
    _quantity(result, name, quantity)
    value = normalize(quantity, "proportion", "")
    _require(not isinstance(value, str), f"{name}: {value}")
    result.normalized_inputs[name] = value
    return value


def _alpha(request: CalculationRequest) -> tuple[float, bool]:
    _require(request.alpha is not None and 0 < request.alpha < 1, "alpha must be in (0, 1)")
    _require(request.sidedness in {"one_sided", "two_sided"}, "sidedness is missing or unsupported")
    return request.alpha, request.sidedness == "two_sided"


def _minimum(at: Callable[[int], float], target: float) -> int:
    _require(0 < target < 1, "target power must be in (0, 1)")
    low, high = 2, 2
    while at(high) < target:
        high *= 2
        _require(high <= 2**30, "required sample exceeds supported search range")
    while low < high:
        middle = (low + high) // 2
        if at(middle) >= target:
            high = middle
        else:
            low = middle + 1
    return low


def _power(value: float) -> float:
    _require(
        math.isfinite(value) and 0 <= value <= 1, "power is outside the finite numerical range"
    )
    return value


def _paired(request: CalculationRequest, result: CalculationResult) -> None:
    alpha, two_sided = _alpha(request)
    _require(
        request.contrast_type in {None, "superiority"},
        "paired planning supports zero-null differences only",
    )
    effect = _quantity(result, "effect", request.effect)
    sd = _quantity(result, "sd", request.sd)
    for quantity in (request.effect, request.sd):
        value = normalize(quantity, "outcome", request.outcome_unit)
        _require(not isinstance(value, str), str(value))
    _require(sd > 0, "SD of paired differences must be positive")
    if two_sided:
        effect = abs(effect)
    else:
        _require(request.direction in {"increase", "decrease"}, "one-sided direction is missing")
        effect *= 1 if request.direction == "increase" else -1
    standardized = effect / sd
    _require(math.isfinite(standardized), "standardized effect exceeds the finite numerical range")
    result.normalized_inputs.update(effect=effect, sd_diff=sd, d=standardized, alpha=alpha)
    result.implementation = "scipy.stats.t.isf + scipy.stats.nct.sf/cdf"
    result.assumptions.extend(
        [
            "Independent pairs; normally distributed paired differences; sd is SD of differences.",
            "Zero-null paired difference, not a pooled two-sample model.",
        ]
    )

    def at(count: int) -> float:
        critical = t.isf(alpha / (2 if two_sided else 1), count - 1)
        nc = standardized * math.sqrt(count)
        power = nct.sf(critical, count - 1, nc)
        if two_sided:
            power += nct.cdf(-critical, count - 1, nc)
        return _power(float(power))

    count = request.n_pairs if request.n_pairs is not None else request.n_per_arm
    _require(
        count is not None or request.target_power is not None, "n_pairs or target_power is required"
    )
    if count is not None:
        _require(count >= 2, "at least two pairs are required")
        result.power, result.df = at(count), count - 1
    if request.target_power is not None:
        _require(
            standardized > 0, "required sample is unattainable for the stated directional effect"
        )
        result.required_n_per_arm = _minimum(at, request.target_power)


def _wilcoxon(request: CalculationRequest, result: CalculationResult) -> None:
    _require(request.target_power is not None, "target_power is required for Wilcoxon planning")
    _paired(request, result)
    result.required_n_per_arm = math.ceil(result.required_n_per_arm / (3 / math.pi))
    result.power, result.df = None, None
    result.implementation += "; normal-shift ARE 3/pi"
    result.assumptions.append(
        "Wilcoxon sample size is a normal location-shift approximation using ARE 3/pi; "
        "it is not exact power and is not valid for arbitrary difference distributions."
    )


def _mcnemar(request: CalculationRequest, result: CalculationResult) -> None:
    alpha, two_sided = _alpha(request)
    p10 = _probability(result, "p_discordant_10", request.p_discordant_10)
    p01 = _probability(result, "p_discordant_01", request.p_discordant_01)
    total, difference = p10 + p01, p10 - p01
    _require(0 < total <= 1, "discordant probabilities must sum to a value in (0, 1]")
    if two_sided:
        difference = abs(difference)
    else:
        _require(request.direction in {"increase", "decrease"}, "one-sided direction is missing")
        difference *= 1 if request.direction == "increase" else -1
    variance = total - difference**2
    _require(variance > 0, "Connor approximation requires positive alternative variance")
    critical = norm.isf(alpha / (2 if two_sided else 1)) * math.sqrt(total)

    def at(count: int) -> float:
        mean = math.sqrt(count) * difference
        power = norm.sf((critical - mean) / math.sqrt(variance))
        if two_sided:
            power += norm.cdf((-critical - mean) / math.sqrt(variance))
        return _power(float(power))

    _require(
        request.n_pairs is not None or request.target_power is not None,
        "n_pairs or target_power is required",
    )
    if request.n_pairs is not None:
        _require(request.n_pairs >= 2, "at least two pairs are required")
        result.power = at(request.n_pairs)
    if request.target_power is not None:
        _require(
            difference > 0, "required sample is unattainable for the stated directional effect"
        )
        result.required_n_per_arm = _minimum(at, request.target_power)
    result.implementation = "scipy.stats.norm.sf/cdf; Connor multinomial normal approximation"
    result.assumptions.extend(
        [
            "Independent matched binary pairs; p10/p01 are probabilities of discordance.",
            "Large-sample approximation without continuity correction; small discordant counts "
            "need method-specific verification and are not guaranteed by this calculation.",
        ]
    )


def _holm(request: CalculationRequest, result: CalculationResult) -> None:
    result.implementation = "Holm-Bonferroni step-down arithmetic"
    if request.p_values is not None:
        values = request.p_values
        _require(
            bool(values) and all(0 <= value <= 1 for value in values), "valid p_values required"
        )
        _require(
            request.family_size in {None, len(values)}, "family_size must match supplied p_values"
        )
        order = sorted(range(len(values)), key=values.__getitem__)
        adjusted, running = [0.0] * len(values), 0.0
        for rank, index in enumerate(order):
            running = max(running, (len(values) - rank) * values[index])
            adjusted[index] = min(1.0, running)
        result.adjusted = adjusted
        result.assumptions.append(
            "Input p_values are supplied assumptions; adjusted p-values retain input order."
        )
    else:
        _require(
            request.family_size is not None and request.family_size >= 1, "family_size is required"
        )
        _require(request.alpha is not None and 0 < request.alpha < 1, "alpha must be in (0, 1)")
        result.adjusted = [
            request.alpha / (request.family_size - rank) for rank in range(request.family_size)
        ]
        result.assumptions.append(
            "adjusted contains alpha thresholds in ascending p-value rank order."
        )


def _a12(request: CalculationRequest, result: CalculationResult) -> None:
    _require(
        (request.a12 is None) != (request.effect is None),
        "supply exactly one of a12 or standardized effect",
    )
    if request.a12 is not None:
        probability = _probability(result, "a12", request.a12)
        _require(0 < probability < 1, "finite normal-shift d needs a12 strictly inside (0, 1)")
        d = math.sqrt(2) * norm.ppf(probability)
    else:
        d = _quantity(result, "effect", request.effect)
        _require(request.effect.unit in {"", "d", "standardized"}, "effect must be standardized d")
        probability = norm.cdf(d / math.sqrt(2))
    result.normalized_inputs.update(a12=float(probability), d=float(d))
    result.implementation = "scipy.stats.norm.ppf/cdf"
    result.assumptions.append("A12-to-d conversion assumes equal-variance normal location shifts.")


def _cliffs_delta(request: CalculationRequest, result: CalculationResult) -> None:
    _require(
        (request.a12 is None) != (request.cliffs_delta is None),
        "supply exactly one of a12 or cliffs_delta",
    )
    if request.cliffs_delta is not None:
        delta = _quantity(result, "cliffs_delta", request.cliffs_delta)
        _require(
            request.cliffs_delta.unit in {"", "delta", "cliffs_delta"},
            "cliffs_delta must be dimensionless",
        )
        _require(-1 <= delta <= 1, "cliffs_delta must be in [-1, 1]")
        probability = (delta + 1) / 2
    else:
        probability = _probability(result, "a12", request.a12)
        delta = 2 * probability - 1
    result.normalized_inputs.update(a12=probability, cliffs_delta=delta)
    result.implementation = "Cliff's delta = 2*A12 - 1 (algebraic)"


def _bootstrap(request: CalculationRequest, result: CalculationResult) -> None:
    _require(
        request.values is not None and len(request.values) >= 2, "at least two values are required"
    )
    _require(
        request.confidence is not None and 0 < request.confidence < 1,
        "confidence must be in (0, 1)",
    )
    _require(
        request.resamples is not None and request.resamples >= 1, "positive resamples required"
    )
    _require(
        request.random_seed is not None and request.random_seed >= 0,
        "explicit nonnegative random_seed required",
    )
    with np.errstate(over="ignore", invalid="ignore"):
        ci = bootstrap(
            (np.asarray(request.values),),
            np.mean,
            confidence_level=request.confidence,
            n_resamples=request.resamples,
            method="percentile",
            batch=min(256, request.resamples),
            rng=np.random.default_rng(request.random_seed),
        ).confidence_interval
    _require(
        math.isfinite(ci.low) and math.isfinite(ci.high),
        "bootstrap interval is outside the finite numerical range",
    )
    result.ci_low, result.ci_high = float(ci.low), float(ci.high)
    result.implementation = "scipy.stats.bootstrap; percentile CI of mean"
    result.assumptions.extend(
        [
            "values are supplied assumptions; source provenance is not established "
            "by this calculator.",
            "IID observations; percentile bootstrap of the arithmetic mean, "
            "not a universal CI procedure.",
        ]
    )


def calculate(request: CalculationRequest) -> CalculationResult:
    raw = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    result = CalculationResult(
        id="calc:" + digest,
        request_hash=digest,
        method_id=request.calculator_id or "unsupported",
        status="not_computed",
        implementation="unavailable",
    )
    functions = {
        "paired_t": _paired,
        "wilcoxon_signed_rank": _wilcoxon,
        "mcnemar": _mcnemar,
        "holm": _holm,
        "a12": _a12,
        "cliffs_delta": _cliffs_delta,
        "bootstrap_ci": _bootstrap,
    }
    try:
        _require(request.calculator_id in functions, "calculator_id is missing or unsupported")
        functions[request.calculator_id](request, result)
    except _NotComputed as exc:
        result.reasons.append(str(exc))
        result.power = result.required_n_per_arm = result.df = None
        result.ci_low = result.ci_high = result.adjusted = None
        return result
    result.status = "computed"
    result.applicability = [
        "Numerical planning under the recorded method-specific assumptions only."
    ]
    return result
