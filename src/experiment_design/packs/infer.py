"""Classify methodology with the stage model; routing remains JEV's responsibility."""

from typing import Literal

from experiment_design.agents.structured import structured_call
from experiment_design.config import RunConfig
from experiment_design.contracts import AgentOutput, Record
from experiment_design.persistence.store import RunStore
from experiment_design.runtime.llm import LLMClient

RESEARCH_TYPES = ("comparative_benchmark", "controlled_experiment", "ablation")


class Inference(Record):
    research_type: Literal[*RESEARCH_TYPES, "unclear"]


def infer_research_type(
    store: RunStore, llm: LLMClient, cfg: RunConfig, brief: AgentOutput
) -> str | None:
    profile = store.state.profile
    if profile is None or profile.research_type is not None:
        return profile.research_type if profile else None
    if store.state.llm_tokens >= store.state.llm_token_limit:
        return None
    result = structured_call(
        store,
        llm,
        cfg,
        "profile_inference",
        {"brief": brief.markdown, "hypotheses": store.read(1, "hypothesis.md")},
        Inference,
    )
    choice = None if result.research_type == "unclear" else result.research_type
    store.update(profile=profile.model_copy(update={"research_type": choice}))
    store.activity(
        "exd.profile.inferred",
        actor="stage",
        summary=f"Research type: {choice or 'unclear'}; mixed designs follow the first hypothesis",
        step_key="s2_framing",
        payload={"research_type": choice, "attempt": store.state.attempt},
    )
    return choice
