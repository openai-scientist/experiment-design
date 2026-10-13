"""Calculator dispatch, constrained by methodology activation rather than scientific scope."""

import hashlib
import json
from collections.abc import Callable

from experiment_design.contracts import CalculationRequest, CalculationResult
from experiment_design.science import power, stats

LEGACY_CALCULATORS = frozenset({"independent_pooled_t", "cluster_equal_size_t"})
CALCULATORS: dict[str, Callable[[CalculationRequest], CalculationResult]] = {
    "independent_pooled_t": power.calculate,
    "cluster_equal_size_t": power.calculate,
    "paired_t": stats.calculate,
    "wilcoxon_signed_rank": stats.calculate,
    "mcnemar": stats.calculate,
    "holm": stats.calculate,
    "a12": stats.calculate,
    "cliffs_delta": stats.calculate,
    "bootstrap_ci": stats.calculate,
}


def calculate(request: CalculationRequest, allowed: frozenset[str]) -> CalculationResult:
    method = request.calculator_id
    if method not in allowed or method not in CALCULATORS:
        raw = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return CalculationResult(
            id="calc:" + digest,
            request_hash=digest,
            method_id=method or "unsupported",
            status="not_computed",
            implementation="unavailable",
            reasons=[
                "calculator not enabled for profile (active pack)"
                if method not in allowed
                else "calculator implementation is unavailable"
            ],
        )
    return CALCULATORS[method](request)
