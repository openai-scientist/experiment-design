"""Designer proposes initial content and coherent, targeted revisions."""

from pydantic import Field

from experiment_design.agents.tools import (
    DecisionProposal,
    ToolContext,
    context_brief,
    decision_proposals,
    source_ids,
    submit_schema,
    validate_refs,
    validate_source_quantities,
)
from experiment_design.contracts import Design, Patch, Record, ResearcherDecision
from experiment_design.patch import apply_patch, initial_design
from experiment_design.runtime.loop import AgentSpec, SubmitRejected

SPEC = AgentSpec("design", "Design a coherent experiment using evidence and scientific tools.")
TOOLS = (
    "read_package",
    "lookup_evidence",
    "read_design",
    "read_issues",
    "run_validators",
    "compute_power",
    "check_patch",
)


class InitialSubmission(Record):
    design: Design
    summary: str = Field(min_length=1)
    decisions: list[DecisionProposal] = Field(default_factory=list)


class Rebuttal(Record):
    issue_id: str
    argument: str
    evidence_refs: list[str] = Field(default_factory=list)


class RevisionSubmission(Record):
    patch: Patch
    rebuttals: list[Rebuttal] = Field(default_factory=list)
    decisions: list[DecisionProposal] = Field(default_factory=list)


def brief(ctx: ToolContext, **focus) -> str:
    if ctx.version is None and ctx.binding:
        focus["researcher_decisions"] = [
            decision.model_dump(mode="json")
            for decision in ctx.store._artifact_records(
                ctx.run_id, "researcher_decision", ResearcherDecision, ctx.binding.event_id
            )
            if decision.purpose == "scientific"
        ]
    return (
        "Researcher-owned claim fields cannot change. Request a decision on /claim/<field>. "
        "Every quantitative input must have honest provenance. Unknown values are assumptions with "
        "a note, never invented sources. Choose methodology through scientific reasoning from "
        "hypothesis, evidence, context and constraints; explain suitability and alternatives. "
        "Tools are optional; unsupported calculator coverage cannot choose your methodology. "
        "For an initial draft, include execution with purpose/inputs/expected outputs and "
        "concrete scientific procedure, rationale, resources and feasibility. "
        "Use relevant validators/computations when warranted. "
        "For initial synthesis, use design.rationale for DesignChoice records (choice, why, "
        "alternatives_considered, tradeoffs). Explain source-fixed choices; methodological "
        "alternatives are proposals. The separate submission.summary must be a short string "
        "summary. Preserve canonical reasons, assumptions and limitations when correcting errors. "
        "Preserve scientific_commitments at their exact design paths and values. "
        "For redesign, reconsider the approach from the selected candidate and critique; "
        "replace obsolete methodology/protocol/analysis coherently with a protected patch. "
        "For revision inspect the committed design before canonical compute_power; sensitivity "
        "overrides are separate observations, never canonical results. Use check_patch; fix the "
        "batch together or rebut with evidence. Inspect the pending researcher decisions, their "
        "immutable replies, affected paths, dependencies and updated context. Reconcile dependent "
        "sampling, measurement, analysis, protocol and handoff in a normal patch. Remove obsolete "
        "requests while preserving confirmed claim values and genuine limitations.\n"
        + context_brief(ctx)
        + "\nFocus: "
        + str(focus)
    )


def submit_tool(ctx: ToolContext, **mode):
    if mode.get("mode", "initial") == "initial":

        def initial(payload):
            if payload.design.execution is None:
                raise SubmitRejected(["A new plan requires explicit execution details"])
            if payload.design.analysis.method_id is not None:
                raise SubmitRejected(["Use calculator_id only in optional computation requests"])
            context = ctx.store.context(
                ctx.run_id, ctx.binding.context_revision if ctx.binding else None
            )
            d = initial_design(ctx.package, payload.design, context=context)
            d = apply_patch(
                d,
                Patch(ops=[], rationale="Validate quantitative provenance"),
                source_ids=source_ids(ctx),
            )
            validate_source_quantities(d, ctx)
            return {
                "design": d.model_dump(mode="json"),
                "rationale": payload.summary,
                "decisions": decision_proposals(payload.decisions, ctx),
            }

        tool = submit_schema("submit_design", InitialSubmission, initial)
        tool.parameters["$defs"]["AnalysisPlan"]["properties"].pop("method_id", None)
        tool.parameters["$defs"]["Design"]["properties"]["execution"] = {
            "$ref": "#/$defs/ExecutionSpec"
        }
        tool.parameters["$defs"]["Design"]["required"].append("execution")
        return tool

    def revision(payload):
        batch = {item.id if hasattr(item, "id") else item for item in mode.get("batch", [])}
        covered = set(payload.patch.addresses) | {r.issue_id for r in payload.rebuttals}
        errors = [
            f"Batch issue {iid} was neither addressed nor rebutted" for iid in batch - covered
        ]
        errors += [f"Issue {iid} is outside this batch" for iid in covered - batch]
        for r in payload.rebuttals:
            if not r.argument.strip() or not r.evidence_refs:
                errors.append(f"Rebuttal {r.issue_id} needs an argument and evidence")
            errors.extend(validate_refs(r.evidence_refs, ctx))
        if errors:
            raise SubmitRejected(errors)
        if ctx.version is None:
            raise SubmitRejected(["Revision needs a committed parent design"])
        changed = apply_patch(
            ctx.version.design,
            payload.patch,
            source_ids=source_ids(ctx),
            context=ctx.store.context(ctx.run_id),
        )
        if changed.execution is not None and changed.analysis.method_id is not None:
            raise SubmitRejected(["Use calculator_id only in optional computation requests"])
        validate_source_quantities(changed, ctx)
        return {
            "patch": payload.patch.model_dump(mode="json"),
            "rebuttals": [r.model_dump(mode="json") for r in payload.rebuttals],
            "decisions": decision_proposals(payload.decisions, ctx),
        }

    return submit_schema("submit_revision", RevisionSubmission, revision)
