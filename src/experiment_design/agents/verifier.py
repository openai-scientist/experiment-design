"""Independent read-only review of actual revisions and possible regressions."""

import json
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
        "For each addressed issue use its supplied comparison. When parent_version is present, "
        "call read_diff from that actual parent to addressed_version. An initial design has no "
        "parent: never request version 0. For initial researcher clarifications, compare the "
        "recorded response and application with read_design and the bound context commitments. "
        "Source provenance does not establish verified district data or scientific acceptance. "
        "Check the "
        "actual diff, original issue and affected paths independently; inspect the latest version "
        "for regressions. Rebutted issues have no patch: judge the argument and cited evidence. "
        "You are read-only; verdicts are recommendations to JEV, never approval.\n"
        + context_brief(ctx)
        + "\nVerification focus: "
        + json.dumps(
            {**focus, "issues": [i.model_dump(mode="json") for i in focus.get("issues", [])]},
            ensure_ascii=False,
        )
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
