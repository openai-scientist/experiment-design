"""Explicit optional pooled-t calculations under declared applicability assumptions."""

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Literal

from scipy.stats import nct, t

from experiment_design.contracts import CalculationRequest, CalculationResult, Quantity


@dataclass(frozen=True)
class Contrast:
    null: float
    alternative: Literal["greater", "less", "two_sided"]
    alpha: float


def contrast_from(request: CalculationRequest) -> Contrast | list[str]:
    reasons = []
    kind, direction = request.contrast_type, request.direction
    if kind not in {"superiority", "non_inferiority", "superiority_by_margin"}:
        reasons.append("contrast_type missing or unsupported")
    if direction not in {"increase", "decrease"}:
        reasons.append("direction missing or unsupported")
    if request.alpha is None or not 0 < request.alpha < 1:
        reasons.append("alpha must be between 0 and 1")
    if request.sidedness not in {"one_sided", "two_sided"}:
        reasons.append("sidedness unsupported")
    margin = request.margin.value if request.margin else None
    if kind in {"non_inferiority", "superiority_by_margin"} and (
        margin is None or not math.isfinite(margin) or margin <= 0
    ):
        reasons.append("margin missing or invalid")
    if reasons:
        return reasons
    sign = 1 if direction == "increase" else -1
    if kind == "superiority":
        alternative = (
            "two_sided"
            if request.sidedness == "two_sided"
            else ("greater" if sign == 1 else "less")
        )
        return Contrast(0, alternative, request.alpha)
    return Contrast(
        (-sign if kind == "non_inferiority" else sign) * margin,
        "greater" if sign == 1 else "less",
        request.alpha,
    )


def design_effect(cluster_size: float, icc: float) -> float:
    return 1 + (cluster_size - 1) * icc


def normalize(
    q: Quantity, expected: Literal["proportion", "outcome"], outcome_unit: str
) -> float | str:
    if q.value is None or not math.isfinite(q.value):
        return "value missing or nonfinite"
    unit = q.unit.strip().casefold()
    if expected == "proportion":
        if unit in {"%", "percent", "percentage"}:
            value = q.value / 100
        elif unit in {"", "proportion", "fraction"}:
            value = q.value
        else:
            return f"unit {q.unit!r} is not a proportion"
        return value if 0 <= value <= 1 else "proportion outside [0, 1]"
    if unit != outcome_unit.strip().casefold():
        return f"unit {q.unit!r} does not match outcome unit {outcome_unit!r}"
    return q.value


def calculate(request: CalculationRequest) -> CalculationResult:
    raw = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    method = request.calculator_id or "unsupported"
    result = CalculationResult(
        id="calc:" + digest,
        request_hash=digest,
        method_id=method,
        status="not_computed",
    )
    reasons = result.reasons
    cluster = request.cluster
    expected_model = {
        "independent_pooled_t": "Pooled two-sample t test",
        "cluster_equal_size_t": "Pooled t test of cluster means",
    }.get(method)
    if expected_model is None:
        result.implementation = "unavailable"
        reasons.append(
            f"Power calculation is not implemented for analysis method {method!r}."
            if method != "unsupported"
            else "Explicit calculator_id is missing; a power implementation cannot be selected."
        )
        result.applicability = [
            "The scientific validity of the SAP was not assessed by this calculator.",
            "Method-specific assumptions and quantitative inputs were not assessed.",
        ]
        return result
    if not request.model:
        reasons.append(
            f"Analysis model is missing for method {method!r}; expected {expected_model!r}."
        )
    elif request.model != expected_model:
        reasons.append(
            f"Analysis method/model mismatch: {method!r} requires {expected_model!r}, "
            f"got {request.model!r}."
        )
    if request.outcome_kind != "continuous":
        reasons.append("method supports continuous outcomes only")
    if request.allocation != "1:1" or request.common_variance is not True:
        reasons.append("method requires equal allocation and explicit common variance assumption")
    if method == "cluster_equal_size_t":
        if cluster is None:
            reasons.append("cluster method requires cluster inputs")
        elif cluster.equal_size is not True or cluster.independent_clusters is not True:
            reasons.append("cluster method requires equal fixed sizes and independent clusters")
        elif (
            request.unit_of_analysis != cluster.unit
            or request.unit_of_randomization != cluster.unit
        ):
            reasons.append("cluster method requires assignment and analysis at cluster level")
    elif method == "independent_pooled_t" and cluster is not None:
        reasons.append("independent method does not support cluster dependence")
    elif method == "independent_pooled_t" and (
        not request.unit_of_analysis or request.unit_of_analysis != request.unit_of_randomization
    ):
        reasons.append("independent method requires matching independent assigned/analysis units")
    quantities = {"effect": request.effect, "sd": request.sd}
    if request.contrast_type in {"non_inferiority", "superiority_by_margin"}:
        quantities["margin"] = request.margin
    if cluster:
        quantities.update(size=cluster.size, icc=cluster.icc)
        if cluster.size and cluster.size.unit.strip().casefold() not in {
            "student",
            "students",
            "participant",
            "participants",
            "person",
            "persons",
            "people",
            "subject",
            "subjects",
            "individual",
            "individuals",
            "animal",
            "animals",
            "observation",
            "observations",
            "unit",
            "units",
            "sample",
            "samples",
        }:
            reasons.append("cluster size unit must denote a count of individuals")
    unit = str(request.outcome_unit or "")
    for name, quantity in quantities.items():
        if quantity is None or quantity.value is None:
            reasons.append(f"{name} missing")
            continue
        result.input_provenance[name] = quantity.provenance
        if quantity.provenance not in {"source", "assumption"}:
            reasons.append(f"{name}: invalid or unattested quantitative provenance")
        if quantity.provenance == "source" and not quantity.source_ref:
            reasons.append(f"{name}: missing source reference")
        value = (
            normalize(
                quantity, "outcome" if name in {"sd", "effect", "margin"} else "proportion", unit
            )
            if name != "size"
            else quantity.value
        )
        if isinstance(value, str):
            reasons.append(f"{name}: {value}")
        else:
            result.normalized_inputs[name] = value
    values = result.normalized_inputs
    if values.get("sd", 0) <= 0:
        reasons.append("sd must be positive")
    if cluster and (values.get("size", 0) < 1 or not float(values.get("size", 0)).is_integer()):
        reasons.append("cluster size must be a positive fixed whole count")
    contrast = contrast_from(request)
    if isinstance(contrast, list):
        reasons.extend(contrast)
    if request.contrast_type != "superiority" and request.sidedness != "one_sided":
        reasons.append("margin method requires one-sided inference")
    if request.target_power is None or not 0 < request.target_power < 1:
        reasons.append("target power must be between zero and one")
    count = cluster.clusters_per_arm if cluster else request.n_per_arm
    if count is not None and count < 2:
        reasons.append("at least two independent units per arm are required")
    if reasons:
        result.applicability = list(reasons)
        return result
    sd = values["sd"]
    if cluster:
        sd *= math.sqrt(values["icc"] + (1 - values["icc"]) / values["size"])
    delta = values["effect"] - contrast.null
    if contrast.alternative == "less":
        delta = -delta
    if contrast.alternative == "two_sided":
        delta = abs(delta)
    if delta <= 0 and request.target_power > contrast.alpha:
        reasons.append("target power is unattainable at the stated effect")
        return result

    def at(n: int) -> float:
        df = 2 * n - 2
        nc = delta / (sd * math.sqrt(2 / n))
        critical = t.isf(contrast.alpha / (2 if contrast.alternative == "two_sided" else 1), df)
        power = nct.sf(critical, df, nc)
        if contrast.alternative == "two_sided":
            power += nct.cdf(-critical, df, nc)
        return float(power)

    high = 2
    while at(high) < request.target_power:
        high *= 2
        if high > 2**30:
            reasons.append("required sample exceeds supported search range")
            return result
    low = 2
    while low < high:
        mid = (low + high) // 2
        if at(mid) >= request.target_power:
            high = mid
        else:
            low = mid + 1
    result.status = "computed"
    result.power = at(count) if count is not None else None
    result.df = 2 * count - 2 if count is not None else None
    result.required_clusters_per_arm = low if cluster else None
    result.required_n_per_arm = int(low * values["size"]) if cluster else low
    result.assumptions = [
        "Normal continuous outcomes; common variance; equal allocation",
        "Fixed equal cluster sizes; independent cluster means"
        if cluster
        else "Independent assigned units",
    ]
    result.applicability = ["Supported pooled t planning model under declared assumptions"]
    return result
