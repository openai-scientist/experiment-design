"""Reusable stage/scientific scenarios with fake external providers only."""

import hashlib

from experiment_design.contracts import (
    ArtifactBinding,
    ResearcherDecision,
    SourceArtifact,
    SourceSnapshot,
)
from experiment_design.persistence.store import Store
from tests.support.builders import design, package
from tests.support.providers import submit, tool_call


def source_snapshot(method="Pooled t test of cluster means"):
    from experiment_design.design.patch import package_claim

    text = "\n".join(str(v) for v in package_claim(package()).values())
    text += (
        "\nMargin 3 points. Standard deviation 10 points. "
        "Class size 25. ICC 0.10. At most 60 classes."
    )
    text += f"\n{method}."
    return SourceSnapshot(
        id="source-1",
        artifacts=[
            SourceArtifact(
                ref="S1",
                path="hypothesis.md",
                text=text,
                sha256=hashlib.sha256(text.encode()).hexdigest(),
            )
        ],
    )


def supported_design():
    d = design()
    d.scenarios[0].sd.source_span = "Standard deviation 10 points"
    d.sampling.cluster.icc.source_span = "ICC 0.10"
    d.analysis.method_id = "cluster_equal_size_t"
    d.analysis.model = "Pooled t test of cluster means"
    d.analysis.common_variance = True
    d.sampling.unit_of_analysis = "class"
    d.sampling.cluster.clusters_per_arm = 25
    d.sampling.cluster.equal_size = d.sampling.cluster.independent_clusters = True
    return d


def plan_body(d):
    """Explicit active-plan metadata for existing scientific scenario fixtures."""
    from experiment_design.contracts import DesignChoice, ExecutionSpec, ExpectedOutput

    d = d.model_copy(deep=True)
    if not d.rationale:
        d.rationale = [
            DesignChoice(
                choice=d.analysis.model,
                why="Estimate the source-stated comparison using its declared units "
                "and assumptions",
                alternatives_considered=[
                    "A different procedure would require revisiting the stated constraints"
                ],
            )
        ]
    if not d.protocol.resources:
        d.protocol.resources = ["Researcher-supplied study materials and measurements"]
    if not d.protocol.feasibility:
        d.protocol.feasibility = "Conduct within the supplied recruitment and term constraints"
    d.execution = ExecutionSpec(
        purpose="Evaluate the supplied research question",
        inputs=["Supplied study materials and source-stated measurements"],
        fixed_scientific_choices=["The specified scientific procedure and analysis"],
        expected_outputs=[
            ExpectedOutput(
                name="study report",
                description="Measurements, analysis and limitations",
                format="Markdown and measurement table",
                validation="Trace reported observations to inputs",
            )
        ],
    )
    return d.model_dump(mode="json")


def source_context(method="Pooled t test of cluster means"):
    from experiment_design.contracts import FieldAnnotation, ResearchContext
    from experiment_design.design.patch import package_claim

    p = package()
    p.hypothesis.method = method
    p.constraints = ["At most 60 classes"]
    context = ResearchContext(
        id="context",
        revision=1,
        source_snapshot_id="source-1",
        package=p,
        field_annotations={
            key: FieldAnnotation(
                status="source-stated",
                source_ref="S1",
                source_span="Margin 3 points" if key == "margin" else str(value),
            )
            for key, value in package_claim(p).items()
            if value is not None
        },
    )
    context.field_annotations["/analysis/model"] = FieldAnnotation(
        status="source-stated",
        source_ref="S1",
        source_span=method,
        value=method,
    )
    return context


def understanding_response():
    return submit(
        "submit_understanding",
        {"context": source_context().model_dump(mode="json"), "decisions": []},
    )


def review_responses():

    return [
        submit("submit_findings", {"findings": [], "summary": "Method"}),
        submit("submit_findings", {"findings": [], "scenarios_checked": [], "summary": "Skeptic"}),
    ]


def normal_responses():
    return [
        understanding_response(),
        submit(
            "submit_design",
            {"design": plan_body(supported_design()), "summary": "Draft"},
        ),
        *review_responses(),
    ]


def inspection_response(calculator_id="cluster_equal_size_t"):
    response = tool_call("read_design", {})
    response.tool_calls.extend(
        tool_call(
            "compute_power",
            {
                "calculator_id": calculator_id,
            },
        ).tool_calls
    )
    return response


def staged(store):
    snapshot = SourceSnapshot(
        id="source-1",
        artifacts=[
            SourceArtifact(ref="main", path="hypothesis.md", text="AI feedback", sha256="abc")
        ],
    )
    run = store.create_staged_run(snapshot, {})
    binding = ArtifactBinding(
        source_snapshot_id=snapshot.id, event_id=store.events(run)[-1].event_id
    )
    return run, binding


def decision_state(tmp_path):
    store = Store(tmp_path / "decision.db")
    run, _ = staged(store)

    context = source_context()
    store.save_context(run, context)
    store.add_version(run, design(), author="design", parent=None)
    d = store.save_decision(
        ResearcherDecision(
            id="D1",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Choose margin",
            affected_paths=["/claim/margin"],
        )
    )
    return store, run, context, d


def seeded_run(store, pkg, config):
    run = store.create_staged_run(source_snapshot(), config)
    context = source_context()
    context.package = pkg
    store.save_context(run, context)
    return run
