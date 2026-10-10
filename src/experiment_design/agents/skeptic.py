"""Stress-test material assumptions and alternative explanations."""

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
    "skeptic", "Test assumptions using counterexamples, evidence and sensitivity tools."
)
TOOLS = ("read_package", "lookup_evidence", "read_design", "read_issues", "compute_power")


class Submission(Record):
    findings: list[Finding]
    scenarios_checked: list[str]
    summary: str


def brief(ctx: ToolContext, **focus) -> str:
    return (
        "Look for counterexamples and alternative explanations; use compute_power overrides for "
        "sensitivity scenarios. Submit at most three new, material concerns. A risk resting on "
        "unknown data has basis unknown_assumption. Other findings need source or tool evidence. "
        "Do not repeat ledger or pending findings; do not inflate severity.\n" + context_brief(ctx)
    )


def submit_tool(ctx: ToolContext, **mode):
    def submit(payload):
        errors = validate_findings(payload.findings, ctx) + validate_refs(
            payload.scenarios_checked, ctx
        )
        if len(payload.findings) > 3:
            errors.append("At most 3 material findings")
        for f in payload.findings:
            if f.basis != "unknown_assumption" and not f.evidence_refs:
                errors.append("A grounded skeptic finding needs source or tool evidence")
        if any(not ref.startswith("tool:") for ref in payload.scenarios_checked):
            errors.append("scenarios_checked must cite accepted tool calls")
        if errors:
            raise SubmitRejected(errors)
        return payload.model_dump(mode="json")

    return submit_schema("submit_findings", Submission, submit)
