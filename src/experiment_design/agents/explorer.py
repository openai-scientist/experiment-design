"""Propose and compare next experiments from immutable supplied sources."""

import json

from experiment_design.agents.tools import (
    ToolContext,
    source_ids,
    sources,
    submit_schema,
    validate_refs,
)
from experiment_design.design.exploration import (
    ExplorationSubmission,
    evidence_refs,
    validate_exploration,
)
from experiment_design.runtime.loop import AgentSpec, Tool

TOOLS = ("read_sources", "lookup_evidence")
SPEC = AgentSpec(
    agent_id="explorer",
    system_prompt="""You design useful next experiments, not a complete EDP.
Explore broadly, probe cheaply, narrow with evidence, preserve learning.
Read the supplied sources and preserve the researcher's exact intent in the source record.
Your interpretation, alternative explanations and candidate questions are provisional proposals.
Seek meaningfully different approaches to important uncertainties, including discrimination,
measurement and feasibility probes. Never claim an exhaustive set of explanations.
Each candidate needs possible observations, their next-decision consequences and inference limits.
If observations cannot discriminate explanations, disclose that; technical or measurement failure
does not refute the efficacy hypothesis. Missing confirmatory fields permit exploration.
Prioritize qualitatively with evidence, constraints, conditions and decisive unknowns.
Ties are valid.
give comparison rationale rather than arbitrary scores. Explain limited breadth when only one route
is supported. Prefer cheap probes with an honest cost basis; keep unknown costs/resources unknown.
Unavailable methods require supplied evidence and must not be recommended. Conditional or unknown
feasibility is a disclosed proposal, never executable authorization. Defer only with a substantive
reason and focused question when a useful next step cannot yet be justified.
Sources and own-task accepted tool evidence_ref values are the only admissible evidence references.
Copy references exactly. Excerpts and methodological suggestions are not verified calculations.
Supplied-source exploration is not external literature discovery. Do not invent evidence, costs,
utility, probabilities, numerical power, researcher answers, approval or scientific verification.
Submit proposals with submit_exploration; do not create a canonical design or execute an experiment.
""",
)


def brief(ctx: ToolContext, **focus) -> str:
    ctx.store.check_binding(ctx.run_id, ctx.binding)
    return "Explore, compare and propose a useful next probe.\n" + json.dumps(
        {
            "binding": ctx.binding.model_dump(mode="json"),
            "sources": [source.model_dump(mode="json") for source in sources(ctx)],
            "review_feedback": [issue.model_dump(mode="json") for issue in focus.get("issues", [])],
            "reference_guidance": "Use exact source IDs or accepted own-task evidence_ref values.",
        },
        ensure_ascii=False,
    )


def submit_tool(ctx: ToolContext, **focus) -> Tool:
    def accept(submission: ExplorationSubmission) -> dict:
        ctx.store.check_binding(ctx.run_id, ctx.binding)
        refs = evidence_refs(submission)
        if errors := validate_refs(sorted(refs), ctx):
            raise ValueError("; ".join(errors))
        validate_exploration(submission, allowed_evidence_refs=refs, source_refs=source_ids(ctx))
        return submission.model_dump(mode="json")

    return submit_schema("submit_exploration", ExplorationSubmission, accept)
