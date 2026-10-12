"""Responsibilities and permitted scientific returns of the seven core stages."""

from experiment_design.contracts import StageId, StageSpec


def stage_specs() -> dict[StageId, StageSpec]:
    rows = [
        ("S1", "Artifact intake", ["Markdown"], "Immutable readable source snapshot", []),
        ("S2", "Exploration and framing", ["SourceSnapshot"], "Grounded selected direction", []),
        ("S3", "Experiment design synthesis", ["ResearchContext"], "Reviewable draft", ["S2"]),
        (
            "S4",
            "Scientific review and refinement",
            ["DesignVersion"],
            "Independent critique, revisions and disclosed limitations",
            ["S2", "S3"],
        ),
        (
            "S5",
            "Design qualification",
            ["DesignVersion", "ReviewIssue"],
            "Readiness assessment",
            ["S2", "S3", "S4"],
        ),
        (
            "S6",
            "Researcher review",
            ["ReadinessAssessment"],
            "Version-bound pending review or researcher response",
            ["S2", "S3", "S4"],
        ),
        (
            "S7",
            "EDP publication",
            ["PublicationSnapshot"],
            "Consistent draft or approved bundle",
            ["S2", "S3", "S4"],
        ),
    ]
    return {
        id: StageSpec(
            id=id,
            objective=objective,
            input_kinds=inputs,
            completion_condition=completion,
            permitted_return_targets=targets,
        )
        for id, objective, inputs, completion, targets in rows
    }
