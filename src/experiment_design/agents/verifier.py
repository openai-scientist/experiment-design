"""Independent read-only review of actual revisions and possible regressions."""

from typing import Literal

from experiment_design.agents.tools import (
    ToolContext,
    context_brief,
    submit_schema,
    validate_findings,
    validate_refs,
)
from experiment_design.contracts import Finding, Record
from experiment_design.runtime.loop import AgentSpec, SubmitRejected

SPEC = AgentSpec("verifier", "Independently verify before/after changes; never write patches.")
TOOLS = (
    "read_design",
    "read_diff",
    "read_issues",
    "run_validators",
    "compute_power",
    "lookup_evidence",
)


class Verdict(Record):
    issue_id: str
    verdict: Literal["resolved", "reopen", "uncertain"]
    grounds: str
    evidence_refs: list[str]


class Submission(Record):
    verdicts: list[Verdict]
    regressions: list[Finding]


def brief(ctx: ToolContext, **focus) -> str:
    return (
        "For each addressed issue call read_diff on addressed_in - 1 → addressed_in. Check the "
        "actual diff, original issue and affected paths independently; inspect the latest version "
        "for regressions. Rebutted issues have no patch: judge the argument and cited evidence. "
        "You are read-only; verdicts are recommendations to JEV, never approval.\n"
        + context_brief(ctx)
        + "\nVerification focus: "
        + str(focus)
    )


def submit_tool(ctx: ToolContext, **mode):
    def submit(payload):
        expected = {item.id if hasattr(item, "id") else item for item in mode.get("issues", [])}
        actual = [v.issue_id for v in payload.verdicts]
        errors = validate_findings(payload.regressions, ctx)
        if set(actual) != expected or len(actual) != len(expected):
            errors.append("Exactly one verdict is required for each issue under verification")
        for verdict in payload.verdicts:
            if not verdict.grounds.strip():
                errors.append(f"Verdict {verdict.issue_id} needs grounds")
            errors.extend(validate_refs(verdict.evidence_refs, ctx))
        if errors:
            raise SubmitRejected(errors)
        return payload.model_dump(mode="json")

    return submit_schema("submit_verdicts", Submission, submit)
