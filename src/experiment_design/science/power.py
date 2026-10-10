"""Normal-approximation power for two-arm contrasts, including clustering."""

import hashlib
import json
import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

from pydantic import Field
from scipy.stats import nct, t

from experiment_design.contracts import (
    ArtifactBinding,
    CalculationRequest,
    CalculationResult,
    Claim,
    Design,
    PowerScenario,
    Quantity,
    Record,
)

NORMAL = NormalDist()


@dataclass(frozen=True)
class Contrast:
    null: float
    alternative: Literal["greater", "less", "two_sided"]
    alpha: float
    null_in_outcome_units: float | None = None


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
    null_in_outcome_units = (-sign if kind == "non_inferiority" else sign) * margin
    if claim.outcome_kind.value == "binary" and str(claim.outcome_unit.value).strip() == "%":
        margin /= 100
    null = (-sign if kind == "non_inferiority" else sign) * margin
    return Contrast(null, "greater" if sign == 1 else "less", alpha, null_in_outcome_units)


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
    if design.analysis.method_id:
        result = calculate(
            CalculationRequest(
                binding=ArtifactBinding(source_snapshot_id="compatibility", event_id=0),
                scenario_id=scenario.name,
                design=design,
                overrides=overrides or {},
            )
        )
        return ScenarioResult(
            name=scenario.name,
            status=result.status,
            power=result.power,
            required_n_per_arm=result.required_n_per_arm,
            required_clusters_per_arm=result.required_clusters_per_arm,
            reasons=result.reasons,
            input_provenance=result.input_provenance,
        )
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
        if quantity.provenance == "computed":
            reasons.append(f"{name} is an unattested computed quantity, not a valid input")
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
        if size is not None and size >= 1 and icc is not None:
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


def calculate(request: CalculationRequest) -> CalculationResult:
    raw = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    design = request.design
    if request.preview_patch:
        from experiment_design.patch import _apply_patch

        ids = {
            q.source_ref for s in design.scenarios for q in (s.effect, s.sd) if q and q.source_ref
        }
        if design.sampling.cluster and design.sampling.cluster.icc:
            ids.add(design.sampling.cluster.icc.source_ref)
        design = _apply_patch(design, request.preview_patch, source_ids=ids, author="preview")
    method = design.analysis.method_id or "unsupported"
    result = CalculationResult(
        id="calc:" + digest,
        scenario_id=request.scenario_id,
        counterfactual=bool(request.overrides or request.preview_patch),
        request_hash=digest,
        binding=request.binding,
        method_id=method,
        status="not_computed",
    )
    reasons = result.reasons
    scenarios = [s for s in design.scenarios if s.name == request.scenario_id]
    if len(scenarios) != 1:
        reasons.append("scenario is missing or ambiguous")
        return result
    scenario = scenarios[0]
    cluster = design.sampling.cluster
    expected_model = {
        "independent_pooled_t": "Pooled two-sample t test",
        "cluster_equal_size_t": "Pooled t test of cluster means",
    }.get(method)
    if expected_model is None or design.analysis.model != expected_model:
        reasons.append("analysis method/model missing, mismatched or unsupported")
    if design.claim.outcome_kind.value != "continuous":
        reasons.append("method supports continuous outcomes only")
    if design.sampling.allocation != "1:1" or design.analysis.common_variance is not True:
        reasons.append("method requires equal allocation and explicit common variance assumption")
    if method == "cluster_equal_size_t":
        if cluster is None:
            reasons.append("cluster method requires cluster inputs")
        elif cluster.equal_size is not True or cluster.independent_clusters is not True:
            reasons.append("cluster method requires equal fixed sizes and independent clusters")
        elif (
            design.sampling.unit_of_analysis != cluster.unit
            or design.sampling.unit_of_randomization != cluster.unit
        ):
            reasons.append("cluster method requires assignment and analysis at cluster level")
    elif method == "independent_pooled_t" and cluster is not None:
        reasons.append("independent method does not support cluster dependence")
    elif (
        method == "independent_pooled_t"
        and design.sampling.unit_of_analysis != design.sampling.unit_of_randomization
    ):
        reasons.append("independent method requires matching independent assigned/analysis units")
    quantities = {"effect": scenario.effect, "sd": scenario.sd}
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
        size_override = request.overrides.get("size")
        if (
            size_override
            and cluster.size
            and (size_override.unit.strip().casefold() != cluster.size.unit.strip().casefold())
        ):
            reasons.append("size override unit differs from canonical cluster-size unit")
    reasons.extend(
        f"unsupported override {key}" for key in request.overrides if key not in quantities
    )
    quantities.update({key: q for key, q in request.overrides.items() if key in quantities})
    unit = str(design.claim.outcome_unit.value or "")
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
            normalize(quantity, "outcome" if name in {"sd", "effect"} else "proportion", unit)
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
    contrast = contrast_from(design.claim, design.analysis.sidedness, design.analysis.alpha)
    if isinstance(contrast, list):
        reasons.extend(contrast)
    if (
        design.claim.contrast_type.value != "superiority"
        and design.analysis.sidedness != "one_sided"
    ):
        reasons.append("margin method requires one-sided inference")
    if not 0 < scenario.target_power < 1:
        reasons.append("target power must be between zero and one")
    count = cluster.clusters_per_arm if cluster else design.sampling.n_per_arm
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
    if delta <= 0 and scenario.target_power > contrast.alpha:
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
    while at(high) < scenario.target_power:
        high *= 2
        if high > 2**30:
            reasons.append("required sample exceeds supported search range")
            return result
    low = 2
    while low < high:
        mid = (low + high) // 2
        if at(mid) >= scenario.target_power:
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
