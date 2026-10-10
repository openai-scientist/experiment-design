"""Fresh read-only inspection of whether downstream work still requires scientific choices."""

from experiment_design.agents.tools import (
    ToolContext,
    context_brief,
    submit_schema,
    validate_findings,
    validate_refs,
)
from experiment_design.contracts import Finding, Record
from experiment_design.runtime.loop import AgentSpec, SubmitRejected

SPEC = AgentSpec(
    "handoff", "Inspect scientific executability independently; never patch or approve."
)
TOOLS = (
    "read_context",
    "read_design",
    "read_issues",
    "lookup_evidence",
    "compute_power",
    "run_validators",
)


class Submission(Record):
    handoff_complete: bool
    risks: list[str]
    findings: list[Finding]
    evidence_refs: list[str]


def brief(ctx: ToolContext, **focus) -> str:
    return (
        "Identify unresolved scientific choices needed for implementation: estimand, outcome, "
        "comparison, sampling, SAP, supported power, uncertainty and stopping. Inspect with tools. "
        "Unsupported SAP or recruitment size is incomplete even if limitations are acknowledged. "
        "Return concrete risks and path-grounded findings; headings alone cannot show readiness.\n"
        + context_brief(ctx)
    )


def submit_tool(ctx: ToolContext, **focus):
    def accept(payload):
        errors = validate_findings(payload.findings, ctx) + validate_refs(
            payload.evidence_refs, ctx
        )
        if payload.handoff_complete and (
            payload.risks or any(f.severity in {"MAJOR", "BLOCKER"} for f in payload.findings)
        ):
            errors.append("Material handoff risks conflict with complete handoff")
        if payload.handoff_complete and not any(
            o.id in ctx.accepted_call_ids
            and o.binding == ctx.binding
            and o.tool_name == "read_design"
            and o.arguments.get("path", "/") == "/"
            for o in ctx.store.accepted_observations(ctx.task_id)
        ):
            errors.append("Complete handoff requires inspection of the bound design")
        if errors:
            raise SubmitRejected(errors)
        return payload.model_dump(mode="json")

    return submit_schema("submit_handoff", Submission, accept)
