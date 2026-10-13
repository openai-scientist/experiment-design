"""Translate a recorded researcher answer into a scoped proposal without inventing intent."""

import json

from experiment_design.agents.tools import ToolContext, context_brief, submit_schema
from experiment_design.contracts import Patch, Record, ResearchContext
from experiment_design.design.decisions import interpret
from experiment_design.design.patch import scientific_path
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
    context_patch: Patch | None = None


def brief(ctx: ToolContext, **focus) -> str:
    decision = focus["decision"]
    return (
        "Interpret this recorded researcher answer only in affected_paths. An alternative is "
        "not constrained to suggested options. If intent is ambiguous, return ambiguous=true, "
        "authorized=false with a precise clarification. Do not change the hypothesis to make "
        "an unsupported calculator run. Before a design exists, prefer context_patch with "
        "only the changed /context/package/... paths and values. Code copies the current "
        "context and advances its revision, retaining sources and metadata exactly. "
        "Do not combine context_patch with context or patch. "
        "After a design exists, supply exact patch operations. Retain unimplemented choices. "
        "Copy the current context before editing: retain source text and field_annotations "
        "verbatim, keep unknowns and decision_ids unchanged, increment revision by one, and "
        "change only authorized scientific fields. Authority is assigned during application. "
        "Context paths and canonical design paths name the same fields; the mapping below "
        "is authoritative. Before a design exists, edit the context side of that mapping. "
        "A confirmation authorizes the stated value, including a missing estimand. Keep "
        "already matching values unchanged; no edit is required for every affected path. "
        "Path mapping (recorded path to canonical design path): "
        + json.dumps({p: scientific_path(p) for p in decision.affected_paths})
        + "\n"
        + json.dumps(decision.model_dump(mode="json"), ensure_ascii=False)
        + "\n"
        + context_brief(ctx)
    )


def submit_tool(ctx: ToolContext, **focus):
    def accept(payload: Submission):
        result = payload.model_dump(mode="json")
        interpreted = interpret(focus["decision"], ctx.store.context(ctx.run_id), result)
        return {k: v for k, v in interpreted.interpretation.items() if k != "response_id"}

    return submit_schema("submit_interpretation", Submission, accept)
