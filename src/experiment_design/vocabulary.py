"""Names shared by the workflow, the API and the engine capabilities."""

from experiment_design.api.contract_models import (
    EXD_EXP_PLAN,
    I2H_HYPOTHESES,
)

STEPS: tuple[tuple[str, str], ...] = (
    ("s1_snapshot", "Hypothesis snapshot"),
    ("s2_framing", "Framing"),
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
OPTIONAL_INTEGRATIONS: tuple[str, ...] = ()
INPUT_SCHEMAS: tuple[str, ...] = (I2H_HYPOTHESES,)
OUTPUT_SCHEMAS: tuple[tuple[str, bool], ...] = ((EXD_EXP_PLAN, True),)
PROFILES: tuple[dict[str, str | None], ...] = ({},)
PUBLISH_EXTRAS: tuple[tuple[str, str], ...] = ()
