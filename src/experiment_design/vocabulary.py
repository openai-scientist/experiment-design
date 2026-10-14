"""Names shared by the workflow, the API and the engine capabilities."""

from experiment_design.api.contract_models import (
    EXD_EXP_PLAN,
    EXD_EXPERIMENT_INDEX,
    EXD_RESOURCES,
    I2H_HYPOTHESES,
)

STEPS: tuple[tuple[str, str], ...] = (
    ("s1_snapshot", "Hypothesis snapshot"),
    ("s2_framing", "Framing"),
    ("s2b_resources", "Resources"),
    ("s3_design", "Design"),
    ("s4_review", "Critic review"),
    ("s5_final_check", "Final check"),
    ("s6_researcher_review", "Researcher review"),
    ("s7_publish", "Publish"),
)

# Part A (S2.5) adds its own agent/route/revision types; part B owns the profile, resources
# and index types below.
ACTIVITY_TYPES: tuple[str, ...] = (
    "exd.agent.started",
    "exd.agent.accepted",
    "exd.calculation.computed",
    "exd.route.decided",
    "exd.review.reframed",
    "exd.revision.started",
    "exd.researcher.responded",
    "exd.profile.inferred",
    "exd.resources.surveyed",
    "exd.resources.selected",
    "exd.resources.acquired",
    "exd.resources.validated",
    "exd.index.checked",
)

# Roles that run on the review model; every other role uses the stage model.
REVIEW_ROLES = frozenset(
    {"methodologist", "skeptic", "methodologist_rejoinder", "skeptic_rejoinder", "final_check"}
)

INTEGRATIONS: tuple[str, ...] = ("typesafe",)
OPTIONAL_INTEGRATIONS: tuple[str, ...] = ("huggingface",)
INPUT_SCHEMAS: tuple[str, ...] = (I2H_HYPOTHESES,)
OUTPUT_SCHEMAS: tuple[tuple[str, bool], ...] = (
    (EXD_EXP_PLAN, True),
    (EXD_RESOURCES, False),
    (EXD_EXPERIMENT_INDEX, False),
)
PROFILES: tuple[dict[str, str | None], ...] = (
    {},
    {"domain": "ml"},
    {"domain": "se"},
    {"research_type": "comparative_benchmark"},
    {"research_type": "controlled_experiment"},
    {"research_type": "ablation"},
)
PUBLISH_EXTRAS: tuple[tuple[str, str], ...] = (("experiment_index.json", EXD_EXPERIMENT_INDEX),)
