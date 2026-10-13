"""A human-first scientific plan and its single consistent frozen JSON snapshot."""

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from experiment_design.contracts import PublicationSnapshot, ResearcherDecision, SourceSnapshot
from experiment_design.design.exploration import SelectedCandidateInput
from experiment_design.persistence.store import Store


def render_plan(snapshot: PublicationSnapshot) -> str:
    d = snapshot.version.design
    lines = [
        "# Experiment Plan",
        "",
        snapshot.context.package.research_question,
        "",
        f"Run: {snapshot.run['run_id']} | Design version: {snapshot.version.version} "
        f"| SHA256: {snapshot.version.design_hash}",
        "",
        f"Approved by {snapshot.approval.approver}"
        if snapshot.approval
        else "Draft — researcher review pending",
        "",
    ]

    def section(title, content):
        content = [v for v in content if v]
        if content:
            lines.extend([f"## {title}", "", *content, ""])

    def entries(record):
        result = []
        for key, value in record.model_dump(mode="json").items():
            if value is not None and value != [] and value != {} and value != "":
                if isinstance(value, list):
                    value = "; ".join(str(v) for v in value)
                elif isinstance(value, dict):
                    value = "; ".join(f"{k}: {v}" for k, v in value.items() if v is not None)
                result.append(f"- {key.replace('_', ' ').capitalize()}: {value}")
        return result

    section(
        "Scientific objective and intent",
        [
            f"- {name.replace('_', ' ').capitalize()}: {field.value} ({field.origin})"
            for name, field in d.claim
            if field.value is not None
        ],
    )
    rationale = []
    for choice in d.rationale:
        rationale += [
            f"- {choice.choice}: {choice.why}",
            f"  Alternatives considered: {'; '.join(choice.alternatives_considered)}"
            if choice.alternatives_considered
            else "",
            f"  Trade-offs: {choice.tradeoffs}" if choice.tradeoffs else "",
        ]
    section("Scientific rationale and methodology", rationale)
    section(
        "Researcher constraints and budget",
        [f"- Constraint: {v}" for v in snapshot.context.package.constraints]
        + (
            [f"- Budget: {snapshot.context.package.budget}"]
            if snapshot.context.package.budget
            else []
        ),
    )
    section(
        "Conditions and sampling",
        [f"- {c.name} ({c.role}): {c.description}" for c in d.conditions] + entries(d.sampling),
    )
    measurement = []
    if d.measurement.primary_metric:
        measurement += ["Primary measurement:", *entries(d.measurement.primary_metric)]
    for metric in d.measurement.secondary_metrics:
        measurement += ["Secondary measurement:", *entries(metric)]
    measurement += [
        f"- Data collection: {d.measurement.data_collection}"
        if d.measurement.data_collection
        else "",
        f"- Missingness: {d.measurement.missingness}" if d.measurement.missingness else "",
    ]
    section("Measurements and observations", measurement)
    section("Analysis and decision rules", entries(d.analysis))
    section(
        "Execution protocol",
        [f"{n}. {step}" for n, step in enumerate(d.protocol.steps, 1)]
        + [f"- Stop condition: {v}" for v in d.protocol.stop_conditions],
    )
    section(
        "Resources and feasibility",
        [f"- {v}" for v in d.protocol.resources]
        + ([d.protocol.feasibility] if d.protocol.feasibility else []),
    )
    if d.execution:
        e = d.execution
        section(
            "Inputs and prerequisites",
            ([e.purpose] if e.purpose else [])
            + [f"- Input: {v}" for v in e.inputs]
            + [f"- Prerequisite: {v}" for v in e.prerequisites],
        )
        section(
            "Expected outputs",
            [
                f"- {o.name} ({o.format}): {o.description}. Validation: {o.validation}"
                for o in e.expected_outputs
            ],
        )
        section(
            "Implementation boundaries",
            [f"- Fixed scientific choice: {v}" for v in e.fixed_scientific_choices]
            + [f"- Implementation freedom: {v}" for v in e.implementation_freedom]
            + [f"- Acceptance check: {v}" for v in e.acceptance_checks]
            + [f"- Clarify with researcher: {v}" for v in e.clarification_triggers]
            + [f"- Not applicable — {k}: {v}" for k, v in e.not_applicable.items()],
        )
    section(
        "Assumptions, risks and limitations",
        [
            f"- {a.id}: {a.statement}"
            + ("; " + "; ".join(entries(a.quantity)) if a.quantity else "")
            for a in d.assumptions
        ]
        + [
            f"- {r.threat}: {r.mechanism}; mitigation: {r.mitigation or 'not specified'}"
            for r in d.validity
        ]
        + [f"- {v}" for v in snapshot.context.package.hypothesis.limitations]
        + [f"- Context uncertainty: {v}" for v in snapshot.context.unknowns],
    )
    section(
        "Planning scenarios (targets are not computed results)",
        [f"- {scenario.name}: " + "; ".join(entries(scenario)) for scenario in d.scenarios],
    )
    section(
        "Optional scientific computations",
        [
            f"- {c.scenario_id}: {c.status}; calculator {c.method_id}; power {c.power}; "
            f"{'; '.join(c.reasons)}; evidence {c.id}"
            for c in snapshot.calculations
        ],
    )
    review = [
        f"- {i.raised_by} / {i.severity} / {i.status}: {i.mechanism}. "
        f"Consequence: {i.consequence}. "
        f"{('Response: ' + i.rebuttal) if i.rebuttal else ''}"
        for i in snapshot.issues
    ]
    if snapshot.review:
        review = [
            f"- {s['agent_id']} (version {s['binding']['design_version']}): "
            f"{s['result']['findings_or_changes'].get('summary', '')}"
            for s in snapshot.review["submissions"]
        ] + review
    if snapshot.assessment:
        a = snapshot.assessment
        review += [
            f"- Review status: {a.review_status}",
            f"- Plan complete: {a.plan_complete}",
            *[f"- Blocker: {v}" for v in a.blockers],
            f"- Independent review records: {', '.join(a.evidence_refs)}",
        ]
    section("Scientific review and qualification", review)
    section("Downstream actions", [f"- {v}" for v in d.handoff])
    section("Researcher decisions", [render_decision(d) for d in snapshot.decisions])
    section(
        "Provenance",
        [
            f"- Immutable source snapshot: {snapshot.binding.source_snapshot_id}",
            f"- Context revision: {snapshot.binding.context_revision}",
            f"- Journal cutoff: {snapshot.binding.event_id}",
            "The JSON contains the canonical design, source text, review findings and recorded "
            "decisions. Downstream implementation and execution are a separate responsibility.",
        ],
    )
    return "\n".join(lines)


def plan_payload(
    snapshot: PublicationSnapshot, source: SourceSnapshot, selected: SelectedCandidateInput | None
) -> dict:
    finals = [
        d
        for d in snapshot.decisions
        if d.purpose == "final_review"
        and d.binding.model_dump(exclude={"event_id"})
        == snapshot.binding.model_dump(exclude={"event_id"})
    ]
    last_action = finals[-1].responses[-1].action if finals and finals[-1].responses else None
    if snapshot.approval:
        publication_status = approval_status = "approved"
    else:
        approval_status = {"REJECT": "rejected", "HOLD": "held"}.get(
            last_action, "pending" if finals else "not_requested"
        )
        publication_status = (
            "blocked"
            if snapshot.assessment and snapshot.assessment.plan_complete is not True
            else "draft"
        )
    return {
        "contract": "exp-plan/v1",
        "schema_version": 2,
        "run_id": snapshot.run["run_id"],
        "version": snapshot.version.version,
        "publication_status": publication_status,
        "approval_status": approval_status,
        "binding": snapshot.binding.model_dump(),
        "source": source.model_dump(mode="json"),
        "context": snapshot.context.model_dump(mode="json"),
        "selected_candidate": selected.model_dump(mode="json") if selected else None,
        "design": snapshot.version.design.model_dump(mode="json"),
        "issues": [i.model_dump(mode="json") for i in snapshot.issues],
        "review": snapshot.review,
        "observations": [o.model_dump(mode="json") for o in snapshot.observations],
        "decisions": [d.model_dump(mode="json") for d in snapshot.decisions],
        "assessment": snapshot.assessment.model_dump(mode="json") if snapshot.assessment else None,
        "approval": snapshot.approval.model_dump(mode="json") if snapshot.approval else None,
        "calculations": [c.model_dump(mode="json") for c in snapshot.calculations],
    }


def export_plan(
    store: Store, run_id: str, out_dir: Path, version: int | None = None
) -> dict[str, Path]:
    snapshot = prepare_publication(store, run_id, version)
    # Resolve direction from the same historical cutoff, never from later live context.
    from experiment_design.design.exploration import ExplorationSnapshot

    directions = [
        s
        for s in store._artifact_records(
            run_id, "exploration_snapshot", ExplorationSnapshot, snapshot.binding.event_id
        )
        if s.binding.source_snapshot_id == snapshot.binding.source_snapshot_id
        and s.binding.context_revision == snapshot.binding.context_revision
        and s.outcome == "recommendation"
    ]
    selected = None
    if directions:
        chosen = directions[-1]
        selected = SelectedCandidateInput(
            snapshot=chosen, candidate_id=chosen.selection.choice.removeprefix("recommend:")
        )
    data = plan_payload(snapshot, store.source_snapshot(run_id), selected)

    def encode(value):
        return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
            "utf-8"
        )

    raw_json = encode(data)
    payloads = {"exp_plan.md": render_plan(snapshot).encode("utf-8"), "exp_plan.json": raw_json}
    manifest = {
        "contract": "exp-plan/v1",
        "schema_version": 2,
        "run_id": run_id,
        "version": snapshot.version.version,
        "publication_status": data["publication_status"],
        "approval_status": data["approval_status"],
        "design_hash": snapshot.version.design_hash,
        "binding": snapshot.binding.model_dump(),
        "support_hash": hashlib.sha256(raw_json).hexdigest(),
        "files": [
            {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            for name, raw in payloads.items()
        ],
    }
    payloads["manifest.json"] = encode(manifest)
    if out_dir.exists():
        try:
            matches = {p.name for p in out_dir.iterdir()} == set(payloads) and all(
                (out_dir / name).read_bytes() == raw for name, raw in payloads.items()
            )
        except OSError:
            matches = False
        if not matches:
            raise ValueError("destination contains a different or incomplete publication")
    else:
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".exp-plan-", dir=out_dir.parent))
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


def render_decision(decision: ResearcherDecision) -> str:
    """Present the recorded choice and response provenance without granting authority."""
    binding = decision.binding
    lines = [
        f"### Decision {decision.id} — revision {decision.revision}",
        "",
        f"- Status: {decision.status}; purpose: {decision.purpose}",
        f"- Question: {decision.problem}",
        f"- Bound to: design version {binding.design_version}; "
        f"context revision {binding.context_revision}; checkpoint {binding.event_id}",
    ]
    if decision.affected_paths:
        lines.append("- Affected scope: " + ", ".join(decision.affected_paths))
    for dependency in decision.dependencies:
        lines.append(f"- Dependency: {dependency}")
    if decision.recommendation:
        lines.append(f"- Recommendation (provisional): {decision.recommendation}")
    for option in decision.options:
        lines.extend(["", f"#### Option {option.id}: {option.label}", ""])
        for path, value in option.proposal.items():
            lines.append(f"- Proposed {path}: {json.dumps(value, ensure_ascii=False)}")
        lines.extend(f"- Consequence: {value}" for value in option.consequences)
        if option.uncertainty:
            lines.append(f"- Uncertainty: {option.uncertainty}")
        if option.evidence_refs:
            lines.append("- Evidence: " + ", ".join(option.evidence_refs))
    lines.append("")
    if not decision.responses:
        lines.append("- Response: No researcher response recorded.")
    for response in decision.responses:
        lines.append(
            f"- Response {response.response_id}, revision {response.revision}: "
            f"{response.action} by {response.researcher} at {response.at.isoformat()}"
        )
        if response.option_id:
            lines.append(f"- Selected option: {response.option_id}")
        if response.text:
            lines.append(f"- Exact reply: {response.text}")
        answer = decision.interpretation.get("clarifications", {}).get(response.response_id)
        if answer:
            lines.append(f"- Plan clarification: {answer['explanation']}")
            if answer.get("evidence_refs"):
                lines.append("- Clarification evidence: " + ", ".join(answer["evidence_refs"]))
        if response.affected_paths:
            lines.append("- Response scope: " + ", ".join(response.affected_paths))
    if decision.verification:
        lines.append("- Verification: " + json.dumps(decision.verification, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def prepare_publication(
    store: Store, run_id: str, version: int | None = None
) -> PublicationSnapshot:
    current = store.publication_snapshot(run_id, version)
    for event in reversed(store.events(run_id)):
        if event.type != "publication" or event.data["manifest"].get("contract") != "exp-plan/v1":
            continue
        binding = event.data["binding"]
        if binding["design_version"] != current.version.version:
            continue
        published = store.publication_snapshot(run_id, event_id=binding["event_id"])
        # Stage progress/publication telemetry does not alter the scientific snapshot.
        if version is not None or published.model_dump(
            exclude={"run", "binding"}
        ) == current.model_dump(exclude={"run", "binding"}):
            return published
    return current
