"""Human-first EDP rendering and three-file, version-bound exports."""

import hashlib
import json
from pathlib import Path

from experiment_design.contracts import (
    Approval,
    DesignVersion,
    Quantity,
    ResearcherRequest,
    ReviewIssue,
    RunStatus,
    ScientificPackage,
    Source,
)
from experiment_design.package import all_sources, package_hash
from experiment_design.patch import quantities
from experiment_design.runtime.store import Store, now
from experiment_design.science.power import compute_scenarios, contrast_from
from experiment_design.workflow import WorkflowError


def _number(value) -> str:
    if value is None:
        return "Not specified"
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _quantity(q: Quantity | None, sources: list[Source]) -> str:
    if q is None or q.value is None:
        return "Not specified"
    known = {s.id: s for s in sources}
    if q.provenance == "source":
        source = known.get(q.source_ref)
        if source:
            label = "researcher input" if source.kind == "researcher_input" else "source"
            provenance = f"({label}: {source.title})"
        else:
            provenance = "(source unavailable)"
    elif q.provenance == "llm":
        provenance = "(LLM interpretation)"
    else:
        provenance = f"({q.provenance})"
    note = f"; {q.note}" if q.note else ""
    return f"{_number(q.value)} {q.unit} {provenance}{note}".strip()


def render_markdown(
    package: ScientificPackage,
    sources: list[Source],
    version: DesignVersion,
    status: RunStatus,
    latest_version: int,
    issues: list[ReviewIssue],
    requests: list[ResearcherRequest],
    *,
    approval: Approval | None = None,
) -> str:
    d = version.design
    lines = [f"# {package.research_question}", ""]
    if version.version < latest_version:
        lines.append(f"Earlier version {version.version}; latest is {latest_version}")
    else:
        lines.append(
            {
                "RUNNING": "Design and scientific review in progress",
                "REVIEWABLE": "Ready for researcher review",
                "NEEDS_RESEARCHER": "Waiting for researcher input",
                "BLOCKED": "Not ready: blocking concerns remain (NO-GO)",
                "INCONCLUSIVE": "Incomplete: scientific review ended without readiness",
                "BUDGET_STOP": "Incomplete: run budget exhausted",
            }[status]
        )
    if (
        approval
        and approval.version == version.version
        and approval.design_hash == version.design_hash
    ):
        lines.append(f"Approved by {approval.approver}")

    def heading(text):
        lines.extend(["", text, ""])

    def item(label, text):
        if text is not None and text != "":
            lines.append(f"- {label}: {text}")

    def claim(name):
        field = getattr(d.claim, name)
        value = (
            _number(field.value)
            if isinstance(field.value, (int, float))
            else (field.value if field.value is not None else "Not specified")
        )
        if field.origin == "designer_default":
            value += " (proposed by designer, not approved)"
        return value

    heading("## 1. Summary and scientific objective")
    for name, label in (
        ("hypothesis", "Hypothesis"),
        ("prediction", "Prediction"),
        ("failure_condition", "Failure condition"),
        ("exposure", "Exposure"),
        ("inference_scope", "Scope"),
        ("primary_outcome", "Primary outcome"),
        ("outcome_unit", "Outcome unit"),
        ("outcome_kind", "Outcome type"),
        ("direction", "Outcome direction"),
    ):
        item(label, claim(name))
    heading("## 2. Design rationale")
    for choice in d.rationale:
        item(choice.choice, choice.why)
        item("Alternatives considered", "; ".join(choice.alternatives_considered))
        item("Trade-offs", choice.tradeoffs)
    heading("## 3. Experimental design")
    for condition in d.conditions:
        item(f"{condition.role.title()} — {condition.name}", condition.description)
    for name, label in (
        ("population", "Population"),
        ("inclusion", "Inclusion"),
        ("exclusion", "Exclusion"),
        ("allocation", "Allocation"),
        ("randomization", "Randomization"),
        ("unit_of_randomization", "Randomization unit"),
        ("unit_of_analysis", "Analysis unit"),
        ("repetitions", "Repetitions"),
    ):
        item(label, getattr(d.sampling, name))
    item("Participants per arm", d.sampling.n_per_arm)
    if d.sampling.cluster:
        c = d.sampling.cluster
        item("Cluster unit", c.unit)
        item("Clusters per arm", c.clusters_per_arm)
        item("Cluster size", _quantity(c.size, sources))
        item("Intraclass correlation", _quantity(c.icc, sources))
    for resource in d.protocol.resources:
        item("Resource", resource)
    for constraint in package.constraints:
        item("Constraint", constraint)
    item("Budget", package.budget)
    item("Feasibility", d.protocol.feasibility)
    heading("## 4. Measurement and protocol")
    metric = d.measurement.primary_metric
    if metric:
        item("Primary measurement", f"{metric.name}: {metric.operationalization} ({metric.unit})")
        item("Timing", metric.timing)
    for metric in d.measurement.secondary_metrics:
        item("Secondary measurement", f"{metric.name}: {metric.operationalization} ({metric.unit})")
        item("Timing", metric.timing)
    item("Data collection", d.measurement.data_collection)
    item("Missing data", d.measurement.missingness)
    for index, step in enumerate(d.protocol.steps, 1):
        lines.append(f"{index}. {step}")
    for stop in d.protocol.stop_conditions:
        item("Protocol stop condition", stop)
    heading("## 5. Statistical analysis plan")
    item("Estimand", claim("estimand"))
    item("Contrast", claim("contrast_type").replace("_", " "))
    if d.claim.contrast_type.value in {"non_inferiority", "superiority_by_margin"}:
        item("Margin", claim("margin") + " " + claim("outcome_unit"))
    contrast = contrast_from(d.claim, d.analysis.sidedness, d.analysis.alpha)
    if isinstance(contrast, list):
        item("Contrast definition", "Not computed: " + "; ".join(contrast))
    else:
        comparison = {
            "greater": "greater than",
            "less": "less than",
            "two_sided": "different from",
        }[contrast.alternative]
        item(
            "Null and alternative",
            f"Treatment minus control is {comparison} {_number(contrast.null)} "
            f"{d.claim.outcome_unit.value or ''}; the null is the complementary condition",
        )
    item("Analysis model", d.analysis.model)
    item("Alpha", str(d.analysis.alpha))
    item("Sidedness", d.analysis.sidedness.replace("_", " "))
    item("Uncertainty", d.analysis.uncertainty)
    item("Decision rule", d.analysis.decision_rule)
    item("Multiplicity", d.analysis.multiplicity)
    item("Analysis stopping rule", d.analysis.stopping)
    for sensitivity in d.analysis.sensitivity:
        item("Sensitivity analysis", sensitivity)
    lines.extend(
        ["", "| Scenario | Inputs | Power | Recruitment per arm | MDE |", "|---|---|---|---|---|"]
    )
    for scenario, result in zip(d.scenarios, compute_scenarios(d), strict=True):
        names = (
            ("effect", "sd")
            if d.claim.outcome_kind.value == "continuous"
            else (
                "p_control",
                "p_treatment",
            )
        )
        inputs = "; ".join(
            f"{name}: {_quantity(getattr(scenario, name), sources)}" for name in names
        )
        if result.status == "not_computed":
            power_text = "Not computed: " + "; ".join(result.reasons)
            recruitment, mde = "Not computed", "Not computed"
        else:
            power_text = f"{result.power:.2f}" if result.power is not None else "No sample size set"
            recruitment = f"{result.required_n_per_arm} participants (computed)"
            if result.required_clusters_per_arm is not None:
                recruitment += f"; {result.required_clusters_per_arm} clusters (computed)"
            mde = _number(result.mde) + " (computed)" if result.mde is not None else "Not computed"
        cells = [scenario.name, inputs, power_text, recruitment, mde]
        lines.append(
            "| "
            + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ") for cell in cells)
            + " |"
        )
    heading("## 6. Validity, assumptions and limitations")
    for risk in d.validity:
        item(risk.threat, risk.mechanism)
        item("Mitigation", risk.mitigation)
    for assumption in d.assumptions:
        text = assumption.statement
        if assumption.quantity:
            text += "; " + _quantity(assumption.quantity, sources)
        item("Assumption", text)
    for limitation in package.hypothesis.limitations:
        item("Limitation", limitation)
    for issue in issues:
        if issue.status in {"open", "unresolved"}:
            lines.append(f"- Unresolved concern: {issue.mechanism} — {issue.consequence}")
    heading("## 7. Researcher actions and handoff")
    if version.version < latest_version:
        lines.append("See the latest version for open researcher actions.")
    else:
        for request in requests:
            if request.status in {"open", "draft"}:
                lines.append(f"- {request.question}")
    for action in d.handoff:
        lines.append(f"- {action}")
    return "\n".join(lines).replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"


def _concerns(issues: list[ReviewIssue], version: int, latest: int) -> list[ReviewIssue]:
    if version == latest:
        return [
            i for i in issues if i.version_raised <= version and i.status in {"open", "unresolved"}
        ]
    return [
        i.model_copy(update={"status": "open"})
        for i in issues
        if i.version_raised <= version
        and i.status != "duplicate"
        and (i.addressed_in is None or i.addressed_in > version)
    ]


def export(
    store: Store,
    run_id: str,
    out_dir: Path,
    version: int | None = None,
) -> dict[str, Path]:
    run = store.run(run_id)
    latest, selected = store.version(run_id), store.version(run_id, version)
    if selected is None or latest is None:
        raise WorkflowError(f"unknown design version {version} for run {run_id}")
    requests = store.requests(run_id)
    sources = all_sources(run.package, requests)
    ids = {s.id for s in sources}
    for _, q in quantities(selected.design.model_dump(mode="json")):
        if q["provenance"] == "source" and q["source_ref"] not in ids:
            raise WorkflowError("Design contains a source reference absent from its export package")
    concerns = _concerns(store.issues(run_id), selected.version, latest.version)
    pending = (
        [r for r in requests if r.status in {"draft", "open"}]
        if selected.version == latest.version
        else []
    )
    approvals = [
        a
        for a in store.approvals(run_id)
        if a.version == selected.version and a.design_hash == selected.design_hash
    ]
    approval = approvals[-1] if approvals else None
    markdown = render_markdown(
        run.package,
        sources,
        selected,
        run.status,
        latest.version,
        concerns,
        pending,
        approval=approval,
    )
    data = {
        "schema_version": 1,
        "stage": "experiment_design",
        "run_id": run_id,
        "version": selected.version,
        "latest_version": latest.version,
        "status": run.status,
        "provenance": {
            "design_sha256": selected.design_hash,
            "package_sha256": package_hash(run.package),
            "approval": approval.model_dump(mode="json") if approval else None,
            "version_created_at": selected.created_at,
            "exported_at": now(),
        },
        "package": run.package.model_dump(mode="json"),
        "researcher_inputs": [
            s.model_dump(mode="json") for s in sources if s.kind == "researcher_input"
        ],
        "design": selected.design.model_dump(mode="json"),
        "computed": [r.model_dump(mode="json") for r in compute_scenarios(selected.design)],
        "open_concerns": [
            {key: getattr(i, key) for key in ("severity", "paths", "mechanism", "consequence")}
            for i in concerns
        ],
        "pending_requests": [
            {key: getattr(r, key) for key in ("kind", "question", "path")} for r in pending
        ],
    }

    def encoded(value):
        return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
            "utf-8"
        )

    payloads = {
        "experiment_design.md": markdown.encode("utf-8"),
        "experiment_design.json": encoded(data),
    }
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "stage": "experiment_design",
        "version": selected.version,
        "files": [
            {
                "path": name,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            for name, raw in payloads.items()
        ],
    }
    payloads["manifest.json"] = encoded(manifest)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, raw in payloads.items():
        paths[name] = out_dir / name
        paths[name].write_bytes(raw)
    return paths
