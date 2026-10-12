"""Independent critique of methodological suitability and scientific alternatives."""

from pydantic import Field

from experiment_design.agents.tools import (
    ToolContext,
    context_brief,
    submit_schema,
    validate_findings,
)
from experiment_design.contracts import Finding, Record
from experiment_design.runtime.loop import AgentSpec, SubmitRejected

SPEC = AgentSpec("methodologist", "Independently assess methodology, assumptions and alternatives.")
TOOLS = (
    "read_package",
    "lookup_evidence",
    "read_design",
    "read_issues",
    "run_validators",
    "compute_power",
)


class Submission(Record):
    findings: list[Finding]
    summary: str = Field(min_length=1)


def brief(ctx: ToolContext, **focus) -> str:
    return (
        "Assess how the chosen methodology answers the question "
        "under the evidence and constraints. "
        "Consider alternatives, assumptions, measurement validity, interpretation and feasibility. "
        "Challenge the direction itself when local refinements cannot address a material flaw. "
        "Explain grounds, consequence and affected paths for concerns; no fixed inferential chain. "
        "Scientific computations are optional capabilities, not a methodology registry. "
        "Computed findings must cite accepted own-task computations, never invented evidence. "
        "Keep acknowledged uncertainty separate from an unjustified scientific claim.\n"
        + context_brief(ctx)
    )


def submit_tool(ctx: ToolContext, **focus):
    def accept(payload):
        errors = validate_findings(payload.findings, ctx)
        if errors:
            raise SubmitRejected(errors)
        return payload.model_dump(mode="json")

    return submit_schema("submit_findings", Submission, accept)
