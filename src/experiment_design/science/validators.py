"""Deterministic findings about scientific form and computation validity."""

from experiment_design.contracts import (
    CalculationResult,
    Design,
    Record,
    ScientificPackage,
    Severity,
)
from experiment_design.design.patch import design_hash, package_claim, quantities, resolve
from experiment_design.science.power import contrast_from


class ValidationFinding(Record):
    code: str
    severity: Severity
    path: str
    message: str
    consequence: str


CONSEQUENCES = {
    "missing_required": "The experimental comparison or inference cannot be reproduced.",
    "claim_changed": "The design answers a different question than the researcher specified.",
    "claim_missing": "The null or alternative hypothesis cannot be determined.",
    "unit_mismatch": "The measured outcome does not represent the specified claim.",
    "cluster_unit_ignored": "Standard errors ignore clustering, so the test is anti-conservative.",
    "unsourced_quantity": "An input cannot be traced to the evidence claimed to support it.",
    "llm_quantity_as_input": "An interpretation is being treated as a quantitative observation.",
    "alpha_sidedness": "The stated test direction conflicts with the scientific contrast.",
    "no_computable_scenario": "Power and recruitment feasibility remain unquantified.",
}


def validate_design(
    design: Design,
    package: ScientificPackage,
    source_ids: set[str] | None = None,
    calculations: list[CalculationResult] | None = None,
) -> list[ValidationFinding]:
    source_ids = {s.id for s in package.sources} if source_ids is None else source_ids
    findings = []

    def add(code, severity, path, message):
        findings.append(
            ValidationFinding(
                code=code,
                severity=severity,
                path=path,
                message=message,
                consequence=CONSEQUENCES[code],
            )
        )

    required = (
        "/claim/hypothesis/value",
        "/measurement/primary_metric",
        "/measurement/data_collection",
        "/analysis/model",
        "/analysis/uncertainty",
        "/analysis/decision_rule",
        "/analysis/stopping",
        "/protocol/steps",
        "/protocol/stop_conditions",
    )
    if design.execution is not None:
        required = (
            "/claim/hypothesis/value",
            "/analysis/model",
            "/measurement/primary_metric",
            "/measurement/data_collection",
            "/analysis/decision_rule",
            "/analysis/stopping",
            "/protocol/steps",
            "/protocol/stop_conditions",
            "/execution/purpose",
            "/execution/inputs",
            "/execution/expected_outputs",
            "/execution/fixed_scientific_choices",
            "/rationale",
            "/analysis/uncertainty",
            "/protocol/resources",
            "/protocol/feasibility",
        )
    for path in required:
        omitted = design.execution.not_applicable.get(path, "").strip() if design.execution else ""
        if omitted and path in {
            "/measurement/primary_metric",
            "/measurement/data_collection",
            "/protocol/resources",
            "/protocol/feasibility",
            "/analysis/uncertainty",
        }:
            continue
        value = resolve(design, path)
        if not value or isinstance(value, str) and not value.strip():
            add(
                "missing_required",
                "MAJOR",
                path.removesuffix("/value"),
                "Required scientific content is missing",
            )
    if design.claim.contrast_type.value is not None and not design.conditions:
        add("missing_required", "MAJOR", "/conditions", "The declared contrast needs comparators")
    if design.sampling.randomization and not design.sampling.unit_of_randomization:
        add(
            "missing_required",
            "MAJOR",
            "/sampling/unit_of_randomization",
            "Assignment unit is missing",
        )
    known = package_claim(package)
    for field, value in known.items():
        claim_field = getattr(design.claim, field)
        if claim_field.origin == "package" and claim_field.value != value:
            add(
                "claim_changed",
                "BLOCKER",
                f"/claim/{field}",
                f"Package-owned {field} differs from the researcher input",
            )
    contrast = (
        contrast_from(design.claim, design.analysis.sidedness, design.analysis.alpha)
        if design.claim.contrast_type.value is not None
        else None
    )
    needed = ["contrast_type", "direction"]
    if design.claim.contrast_type.value in {"non_inferiority", "superiority_by_margin"}:
        needed.append("margin")
    for field in needed if design.claim.contrast_type.value is not None else []:
        if isinstance(contrast, list) and getattr(design.claim, field).origin == "missing":
            add("claim_missing", "MAJOR", f"/claim/{field}", f"{field} is needed for the contrast")
    metric = design.measurement.primary_metric
    if (
        metric
        and design.claim.primary_outcome.value is not None
        and (
            metric.name.strip().casefold()
            != str(design.claim.primary_outcome.value or "").strip().casefold()
            or metric.unit.strip().casefold()
            != str(design.claim.outcome_unit.value or "").strip().casefold()
            or metric.kind != design.claim.outcome_kind.value
        )
    ):
        add(
            "unit_mismatch",
            "MAJOR",
            "/measurement/primary_metric",
            "Metric unit or kind differs from the claim",
        )
    sampling = design.sampling
    if (
        sampling.unit_of_randomization
        and sampling.unit_of_analysis
        and sampling.unit_of_randomization != sampling.unit_of_analysis
        and sampling.cluster is None
    ):
        add(
            "cluster_unit_ignored",
            "BLOCKER",
            "/sampling/cluster",
            "Randomization and analysis units differ without a clustering specification",
        )
    for path, q in quantities(design.model_dump(mode="json")):
        if q["provenance"] == "source" and q["source_ref"] not in source_ids:
            add("unsourced_quantity", "MAJOR", path, f"Unknown source {q['source_ref']}")
        if q["provenance"] == "llm" and (
            path.startswith("/scenarios/") or path.startswith("/sampling/cluster/")
        ):
            add("llm_quantity_as_input", "MAJOR", path, "LLM interpretation is not a valid input")
    if (
        design.claim.contrast_type.value in {"non_inferiority", "superiority_by_margin"}
        and design.analysis.sidedness == "two_sided"
    ):
        add(
            "alpha_sidedness",
            "MAJOR",
            "/analysis/sidedness",
            "This contrast requires one-sided inference",
        )
    results = calculations or []
    if calculations is not None and any(
        r.binding.design_hash != design_hash(design) for r in calculations
    ):
        add(
            "no_computable_scenario",
            "MAJOR",
            "/scenarios",
            "Calculation binding differs from design",
        )
    if results and all(r.status == "not_computed" for r in results):
        add("no_computable_scenario", "MINOR", "/scenarios", "No power scenario can be computed")
    return findings
