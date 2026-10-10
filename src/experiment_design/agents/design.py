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
from experiment_design.contracts import Design, Patch, Record
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
    rationale: str
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
    return (
        "Researcher-owned claim fields cannot change. Request a decision on /claim/<field>. "
        "Every quantitative input must have honest provenance. Unknown values are assumptions with "
        "a note, never invented sources. Inspect evidence and compute_power before submitting. "
        "For an initial draft, pass your proposed design to compute_power and run_validators. "
        "For revision use check_patch; fix the batch together or rebut with evidence.\n"
        + context_brief(ctx)
        + "\nFocus: "
        + str(focus)
    )


def submit_tool(ctx: ToolContext, **mode):
    if mode.get("mode", "initial") == "initial":

        def initial(payload):
            d = initial_design(ctx.package, payload.design, context=ctx.store.context(ctx.run_id))
            d = apply_patch(
                d,
                Patch(ops=[], rationale="Validate quantitative provenance"),
                source_ids=source_ids(ctx),
            )
            validate_source_quantities(d, ctx)
            return {
                "design": d.model_dump(mode="json"),
                "rationale": payload.rationale,
                "decisions": decision_proposals(payload.decisions, ctx),
            }

        return submit_schema("submit_design", InitialSubmission, initial)

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
        validate_source_quantities(changed, ctx)
        return {
            "patch": payload.patch.model_dump(mode="json"),
            "rebuttals": [r.model_dump(mode="json") for r in payload.rebuttals],
            "decisions": decision_proposals(payload.decisions, ctx),
        }

    return submit_schema("submit_revision", RevisionSubmission, revision)
