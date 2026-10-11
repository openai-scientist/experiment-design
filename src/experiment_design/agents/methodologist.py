"""Methodological review traces each link from hypothesis to decision rule."""

from typing import Literal

from pydantic import field_validator

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
    "methodologist", "Trace the inferential chain and recompute scientific quantities."
)
TOOLS = (
    "read_package",
    "lookup_evidence",
    "read_design",
    "read_issues",
    "run_validators",
    "compute_power",
)
LINK_PATHS = {
    "hypothesis→estimand": ("/claim/hypothesis", "/claim/estimand"),
    "estimand→unit": ("/claim/estimand", "/sampling/unit"),
    "unit→sampling": ("/sampling",),
    "sampling→metric": ("/sampling", "/measurement"),
    "metric→analysis": ("/measurement", "/analysis"),
    "analysis→decision_rule": ("/analysis",),
}


class ChainLink(Record):
    link: str
    status: Literal["ok", "broken", "unclear"]
    note: str
    evidence_refs: list[str]

    @field_validator("link")
    @classmethod
    def canonical_link(cls, value: str) -> str:
        # The brief names scientific concepts; submissions persist the six canonical keys.
        names = {
            "sampling/allocation": "sampling",
            "SAP": "analysis",
            "uncertainty/decision rule": "decision_rule",
        }
        return "→".join(names.get(node.strip(), node.strip()) for node in value.split("→"))


class Submission(Record):
    chain: list[ChainLink]
    findings: list[Finding]
    summary: str


def brief(ctx: ToolContext, **focus) -> str:
    return (
        "Trace hypothesis → estimand → unit → sampling/allocation → metric → SAP → "
        "uncertainty/decision rule. Recompute with tools. Report only errors that could change a "
        "conclusion, with mechanism and consequence. Use the issue ledger; mark duplicates "
        "with duplicate_of. Submit exactly the six named links.\n" + context_brief(ctx)
    )


def submit_tool(ctx: ToolContext, **mode):
    def submit(payload):
        links = [link.link for link in payload.chain]
        errors = validate_findings(payload.findings, ctx)
        if set(links) != set(LINK_PATHS) or len(links) != len(LINK_PATHS):
            errors.append("Chain requires exactly: " + ", ".join(LINK_PATHS))
        for link in payload.chain:
            errors.extend(validate_refs(link.evidence_refs, ctx))
            if link.status == "broken" and not any(
                path.startswith(prefix)
                for f in payload.findings
                for path in f.paths
                for prefix in LINK_PATHS.get(link.link, ())
            ):
                errors.append(f"broken link {link.link} needs a covering finding")
        if errors:
            raise SubmitRejected(errors)
        return payload.model_dump(mode="json")

    return submit_schema("submit_findings", Submission, submit)
