"""Normal-approximation power for two-arm contrasts, including clustering."""

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

from pydantic import Field

from experiment_design.contracts import Claim, Design, PowerScenario, Quantity, Record

NORMAL = NormalDist()


@dataclass(frozen=True)
class Contrast:
    null: float
    alternative: Literal["greater", "less", "two_sided"]
    alpha: float


class ScenarioResult(Record):
    name: str
    status: Literal["computed", "not_computed"]
    power: float | None = None
    required_n_per_arm: int | None = None
    required_clusters_per_arm: int | None = None
    mde: float | None = None
    design_effect: float | None = None
    reasons: list[str] = Field(default_factory=list)
    input_provenance: dict[str, str] = Field(default_factory=dict)


def contrast_from(claim: Claim, sidedness: str, alpha: float) -> Contrast | list[str]:
    reasons = []
    kind, direction = claim.contrast_type.value, claim.direction.value
    if kind not in {"superiority", "non_inferiority", "superiority_by_margin"}:
        reasons.append("contrast_type missing or unsupported")
    if direction not in {"increase", "decrease"}:
        reasons.append("direction missing or unsupported")
    if not 0 < alpha < 1:
        reasons.append("alpha must be between 0 and 1")
    if sidedness not in {"one_sided", "two_sided"}:
        reasons.append("sidedness unsupported")
    margin = claim.margin.value
    if kind in {"non_inferiority", "superiority_by_margin"} and (
        not isinstance(margin, (int, float)) or not math.isfinite(margin) or margin <= 0
    ):
        reasons.append("margin missing or invalid")
    if reasons:
        return reasons
    sign = 1 if direction == "increase" else -1
    if kind == "superiority":
        alternative = (
            "two_sided" if sidedness == "two_sided" else ("greater" if sign == 1 else "less")
        )
        return Contrast(0, alternative, alpha)
    if claim.outcome_kind.value == "binary" and str(claim.outcome_unit.value).strip() == "%":
        margin /= 100
    null = (-sign if kind == "non_inferiority" else sign) * margin
    return Contrast(null, "greater" if sign == 1 else "less", alpha)


def design_effect(cluster_size: float, icc: float) -> float:
    return 1 + (cluster_size - 1) * icc


def power(effect: float, se: float, c: Contrast) -> float:
    t = (effect - c.null) / se
    if c.alternative == "two_sided":
        z = NORMAL.inv_cdf(1 - c.alpha / 2)
        return NORMAL.cdf(t - z) + NORMAL.cdf(-t - z)
    z = NORMAL.inv_cdf(1 - c.alpha)
    return NORMAL.cdf((t if c.alternative == "greater" else -t) - z)


def se_mean_diff(sd: float, n_per_arm: float) -> float:
    return sd * math.sqrt(2 / n_per_arm)


def se_prop_diff(p_treat: float, p_ctrl: float, n_per_arm: float) -> float:
    return math.sqrt((p_treat * (1 - p_treat) + p_ctrl * (1 - p_ctrl)) / n_per_arm)


def normalize(
    q: Quantity, expected: Literal["proportion", "outcome"], outcome_unit: str
) -> float | str:
    if q.value is None:
        return "value missing"
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


def compute_scenario(
    design: Design,
    scenario: PowerScenario,
    overrides: dict[str, Quantity] | None = None,
) -> ScenarioResult:
    inputs = {
        "effect": scenario.effect,
        "sd": scenario.sd,
        "p_control": scenario.p_control,
        "p_treatment": scenario.p_treatment,
    }
    cluster = design.sampling.cluster
    if cluster:
        inputs.update(size=cluster.size, icc=cluster.icc)
    overrides = overrides or {}
    reasons = [f"unsupported override: {key}" for key in overrides if key not in inputs]
    inputs.update({k: v for k, v in overrides.items() if k in inputs})
    provenance = {}
    unit = str(design.claim.outcome_unit.value or "")

    def value(name, expected):
        quantity = inputs[name]
        if quantity is None or quantity.value is None:
            reasons.append(
                "icc missing for cluster-randomized design" if name == "icc" else f"{name} missing"
            )
            return None
        provenance[name] = quantity.provenance
        if quantity.provenance == "llm":
            reasons.append(f"{name} is an LLM interpretation, not a valid input")
            return None
        result = normalize(quantity, expected, unit) if expected else quantity.value
        if isinstance(result, str):
            reasons.append(f"{name}: {result}")
            return None
        return result

    c = contrast_from(design.claim, design.analysis.sidedness, design.analysis.alpha)
    if isinstance(c, list):
        reasons.extend(c)
    if not 0 < scenario.target_power < 1:
        reasons.append("target_power must be between 0 and 1")
    outcome_kind = design.claim.outcome_kind.value
    if outcome_kind == "continuous":
        effect, sd = value("effect", "outcome"), value("sd", "outcome")
        if sd is not None and sd <= 0:
            reasons.append("sd must be positive")

        def se_fn(n):
            return se_mean_diff(sd, n)
    elif outcome_kind == "binary":
        ctrl, treat = value("p_control", "proportion"), value("p_treatment", "proportion")
        effect = treat - ctrl if treat is not None and ctrl is not None else None
        if (
            ctrl is not None
            and treat is not None
            and (ctrl * (1 - ctrl) + treat * (1 - treat) == 0)
        ):
            reasons.append("proportions imply zero sampling variance")

        def se_fn(n):
            return se_prop_diff(treat, ctrl, n)
    else:
        reasons.append("outcome_kind missing or unsupported")
        effect = None
    deff, size, n = 1.0, None, design.sampling.n_per_arm
    if cluster:
        size, icc = value("size", None), value("icc", "proportion")
        if size is not None and (size < 1 or not size.is_integer()):
            reasons.append("cluster size must be a positive whole head count")
        if size is not None and icc is not None:
            deff = design_effect(size, icc)
        n = (
            cluster.clusters_per_arm * size / deff
            if (cluster.clusters_per_arm is not None and size is not None)
            else None
        )
    if n is not None and n <= 0:
        reasons.append("sample size must be positive")
    if not isinstance(c, list) and effect is not None:
        distance = effect - c.null
        favorable = distance if c.alternative != "less" else -distance
        if c.alternative == "two_sided":
            favorable = abs(distance)
        if favorable <= 0 and scenario.target_power > c.alpha:
            reasons.append("target power is unattainable at the stated effect")
    if reasons:
        return ScenarioResult(
            name=scenario.name,
            status="not_computed",
            reasons=reasons,
            input_provenance=provenance,
        )
    high = 1
    while power(effect, se_fn(high), c) < scenario.target_power:
        high *= 2
        if high > 2**40:
            return ScenarioResult(
                name=scenario.name,
                status="not_computed",
                reasons=["required sample size exceeds supported search range"],
                input_provenance=provenance,
            )
    low = 1
    while low < high:
        mid = (low + high) // 2
        if power(effect, se_fn(mid), c) >= scenario.target_power:
            high = mid
        else:
            low = mid + 1
    clusters = math.ceil(low * deff / size) if cluster else None
    required = int(clusters * size) if cluster else low
    mde = None
    if outcome_kind == "continuous" and n is not None:
        z = NORMAL.inv_cdf(1 - c.alpha / (2 if c.alternative == "two_sided" else 1))
        sign = -1 if c.alternative == "less" else 1
        mde = c.null + sign * (z + NORMAL.inv_cdf(scenario.target_power)) * se_fn(n)
    return ScenarioResult(
        name=scenario.name,
        status="computed",
        power=power(effect, se_fn(n), c) if n is not None else None,
        required_n_per_arm=required,
        required_clusters_per_arm=clusters,
        mde=mde,
        design_effect=deff,
        input_provenance=provenance,
    )


def compute_scenarios(design: Design) -> list[ScenarioResult]:
    return [compute_scenario(design, scenario) for scenario in design.scenarios]
