"""Names shared by the workflow, the API and the engine capabilities."""

# Part A (S2.5) adds its own agent/route/revision types; part B owns the profile, resources
# and index types below.
ACTIVITY_TYPES: tuple[str, ...] = (
    "exd.profile.inferred",
    "exd.resources.surveyed",
    "exd.resources.selected",
    "exd.resources.acquired",
    "exd.resources.validated",
    "exd.index.checked",
)
