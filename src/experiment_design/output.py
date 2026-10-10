"""Human-first EDP rendering and three-file, version-bound exports."""

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from experiment_design.contracts import (
    Approval,
    DesignVersion,
    PublicationSnapshot,
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
    return str(int(value)) if float(value).is_integer() else f"{value:.8g}"


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


def render_legacy_markdown(
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
        display_null = (
            contrast.null_in_outcome_units
            if contrast.null_in_outcome_units is not None
            else contrast.null
        )
        item(
            "Null and alternative",
            f"Treatment minus control is {comparison} {_number(display_null)} "
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
        elif issue.status in {"addressed", "rebutted"}:
            lines.append(f"- Awaiting verification: {issue.mechanism} — {issue.consequence}")
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


def render_markdown(snapshot: PublicationSnapshot | ScientificPackage, *args, **kwargs) -> str:
    if isinstance(snapshot, ScientificPackage):
        return render_legacy_markdown(snapshot, *args, **kwargs)
    d = snapshot.version.design
    p = snapshot.context.package
    lines = [
        f"# {p.research_question}",
        "",
        f"Approved by {snapshot.approval.approver}"
        if snapshot.approval
        else "Draft — not approved",
        f"Design version {snapshot.version.version}",
    ]

    def section(title):
        lines.extend(["", title, ""])

    def item(label, value):
        lines.append(f"- {label}: {value if value not in {None, ''} else 'Not specified'}")

    def claim(name):
        field = getattr(d.claim, name)
        value = _number(field.value) if isinstance(field.value, (int, float)) else field.value
        return str(value or "Not specified") + (
            " (provisional; researcher decision needed)"
            if field.origin in {"designer_default", "missing"}
            else ""
        )

    section("## 1. Research intent and scientific conclusion")
    for name in ("hypothesis", "prediction", "failure_condition", "inference_scope", "estimand"):
        item(name.replace("_", " ").capitalize(), claim(name))
    section("## 2. Design rationale and conditions")
    for choice in d.rationale:
        item(choice.choice, choice.why)
        item("Trade-offs", choice.tradeoffs)
    for condition in d.conditions:
        item(f"{condition.name} ({condition.role})", condition.description)
    section("## 3. Sampling, allocation and assignment")
    for name in (
        "population",
        "inclusion",
        "exclusion",
        "allocation",
        "randomization",
        "unit_of_randomization",
        "unit_of_analysis",
        "repetitions",
        "n_per_arm",
    ):
        item(name.replace("_", " ").capitalize(), getattr(d.sampling, name))
    if d.sampling.cluster:
        cluster = d.sampling.cluster
        item("Clusters per arm", cluster.clusters_per_arm)
        item("Cluster size", _quantity(cluster.size, p.sources))
        item("ICC", _quantity(cluster.icc, p.sources))
    section("## 4. Measurement and missing data")
    metric = d.measurement.primary_metric
    if metric:
        item(
            "Primary outcome",
            f"{metric.name} ({metric.unit}), {metric.operationalization}; {metric.timing}",
        )
    for metric in d.measurement.secondary_metrics:
        item("Secondary measure", f"{metric.name}: {metric.operationalization}")
    item("Data collection", d.measurement.data_collection)
    item("Missingness", d.measurement.missingness)
    section("## 5. Statistical analysis and uncertainty")
    for name in (
        "method_id",
        "model",
        "sidedness",
        "alpha",
        "uncertainty",
        "decision_rule",
        "multiplicity",
        "stopping",
    ):
        item(name.replace("_", " ").capitalize(), getattr(d.analysis, name))
    item("Contrast", claim("contrast_type"))
    item("Margin", claim("margin") + " " + claim("outcome_unit"))
    for sensitivity in d.analysis.sensitivity:
        item("Sensitivity", sensitivity)
    section("## 6. Power and recruitment feasibility")
    if not snapshot.calculations:
        lines.append("Not computed: no version-bound production calculation is available.")
    for c in snapshot.calculations:
        if c.status != "computed":
            lines.append("Not computed: " + "; ".join(c.reasons))
            continue
        item("Planning method", c.method_id)
        item("Power", _number(c.power))
        item("Required participants per arm", c.required_n_per_arm)
        if c.required_clusters_per_arm is not None:
            item("Required clusters per arm", c.required_clusters_per_arm)
        item("Degrees of freedom", c.df)
        for name, value in c.normalized_inputs.items():
            item(f"{name} ({c.input_provenance.get(name, 'unknown')})", _number(value))
        for assumption in c.assumptions:
            item("Planning assumption", assumption)
        lines.append(
            "Power is computed under these assumptions. MDE/precision are not reference-validated."
        )
    section("## 7. Execution protocol and stopping policy")
    for number, step in enumerate(d.protocol.steps, 1):
        lines.append(f"{number}. {step}")
    for resource in d.protocol.resources:
        item("Resource", resource)
    item("Feasibility", d.protocol.feasibility)
    for stop in d.protocol.stop_conditions:
        item("Stop condition", stop)
    section("## 8. Validity, assumptions and limitations")
    for risk in d.validity:
        item(risk.threat, f"{risk.mechanism}; mitigation: {risk.mitigation or 'Not specified'}")
    for issue in snapshot.issues:
        if issue.status not in {"resolved", "duplicate"}:
            item(issue.status, f"{issue.mechanism}; {issue.consequence}")
    for assumption in d.assumptions:
        item("Assumption", assumption.statement)
    for limitation in p.hypothesis.limitations:
        item("Limitation", limitation)
    section("## 9. Researcher decisions and readiness")
    for decision in snapshot.decisions:
        item(decision.status, decision.problem)
        for response in decision.responses:
            item(
                f"{response.action} by {response.researcher}",
                response.text or response.option_id or "Exact version",
            )
    if snapshot.assessment:
        item("Review status", snapshot.assessment.review_status)
        item(
            "Scientific handoff",
            "Complete" if snapshot.assessment.handoff_complete else "Incomplete",
        )
        for blocker in snapshot.assessment.blockers:
            item("Blocker", blocker)
    else:
        item("Qualification", "Missing or stale")
    for action in d.handoff:
        item("Handoff", action)
    return "\n".join(lines).rstrip() + "\n"


def _export_snapshot(
    store: Store, run_id: str, out_dir: Path, version: int | None
) -> dict[str, Path]:
    snapshot = store.publication_snapshot(run_id, version)
    support = snapshot.model_dump(mode="json", exclude={"run", "binding"})
    support_hash = hashlib.sha256(json.dumps(support, sort_keys=True).encode()).hexdigest()
    data = {
        "schema_version": 2,
        "run_id": run_id,
        "version": snapshot.version.version,
        "binding": snapshot.binding.model_dump(),
        "context": snapshot.context.model_dump(mode="json"),
        "design": snapshot.version.design.model_dump(mode="json"),
        "computed": [c.model_dump(mode="json") for c in snapshot.calculations],
        "provenance": {
            "design_sha256": snapshot.version.design_hash,
            "approval": snapshot.approval.model_dump(mode="json") if snapshot.approval else None,
        },
        "issues": [i.model_dump(mode="json") for i in snapshot.issues],
        "decisions": [d.model_dump(mode="json") for d in snapshot.decisions],
        "assessment": snapshot.assessment.model_dump(mode="json") if snapshot.assessment else None,
    }

    def encoded(obj):
        return (json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
            "utf-8"
        )

    payloads = {
        "experiment_design.md": render_markdown(snapshot).encode("utf-8"),
        "experiment_design.json": encoded(data),
    }
    manifest = {
        "schema_version": 2,
        "run_id": run_id,
        "version": snapshot.version.version,
        "design_hash": snapshot.version.design_hash,
        "binding": snapshot.binding.model_dump(),
        "support_hash": support_hash,
        "files": [
            {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            for name, raw in payloads.items()
        ],
    }
    payloads["manifest.json"] = encoded(manifest)
    if out_dir.exists():
        try:
            existing = json.loads((out_dir / "manifest.json").read_bytes())
            matches = existing["support_hash"] == support_hash and all(
                hashlib.sha256((out_dir / f["path"]).read_bytes()).hexdigest() == f["sha256"]
                for f in existing["files"]
            )
        except (OSError, ValueError, KeyError):
            matches = False
        if not matches:
            raise ValueError("destination contains a different or incomplete publication")
        manifest = existing
    else:
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".edp-", dir=out_dir.parent))
        try:
            for name, raw in payloads.items():
                (temporary / name).write_bytes(raw)
            temporary.rename(out_dir)
        finally:
            if temporary.exists():
                if temporary.resolve().parent != out_dir.parent.resolve():
                    raise ValueError("temporary publication path escaped its parent")
                shutil.rmtree(temporary)
    store.record_publication(run_id, snapshot.binding, manifest)
    return {name: out_dir / name for name in payloads}


def _concerns(issues: list[ReviewIssue], version: int, latest: int) -> list[ReviewIssue]:
    if version == latest:
        return [
            i
            for i in issues
            if i.version_raised <= version
            and i.status in {"open", "unresolved", "addressed", "rebutted"}
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
    if run.schema_version == 2:
        return _export_snapshot(store, run_id, Path(out_dir), version)
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
    markdown = render_legacy_markdown(
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
