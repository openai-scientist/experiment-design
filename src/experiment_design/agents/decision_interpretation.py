"""Translate a recorded researcher answer into a scoped proposal without inventing intent."""

import json

from experiment_design.agents.tools import ToolContext, context_brief, submit_schema
from experiment_design.contracts import Patch, Record, ResearchContext
from experiment_design.runtime.loop import AgentSpec

SPEC = AgentSpec(
    "decision_interpretation",
    "Interpret the recorded researcher response; preserve its exact intent.",
)
TOOLS = ("read_sources", "read_context", "read_design", "lookup_evidence", "compute_power")


class Submission(Record):
    authorized: bool
    ambiguous: bool
    explanation: str
    patch: Patch | None = None
    context: ResearchContext | None = None


def brief(ctx: ToolContext, **focus) -> str:
    decision = focus["decision"]
    return (
        "Interpret this recorded researcher answer only in affected_paths. An alternative is "
        "not constrained to suggested options. If intent is ambiguous, return ambiguous=true, "
        "authorized=false with a precise clarification. Do not change the hypothesis to make "
        "an unsupported calculator run. Before a design exists, supply a new context revision. "
        "After a design exists, supply exact patch operations. Retain unimplemented choices.\n"
        + json.dumps(decision.model_dump(mode="json"), ensure_ascii=False)
        + "\n"
        + context_brief(ctx)
    )


def submit_tool(ctx: ToolContext, **focus):
    return submit_schema("submit_interpretation", Submission, lambda p: p.model_dump(mode="json"))
