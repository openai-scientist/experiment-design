"""Interpret supplied research sources without conferring researcher authority."""

import json
import re

from pydantic import Field

from experiment_design.agents.tools import ToolContext, sources, submit_schema
from experiment_design.contracts import Record, ResearchContext, ResearcherDecision
from experiment_design.patch import package_claim, resolve
from experiment_design.runtime.loop import AgentSpec

SPEC = AgentSpec(
    "understanding", "Understand the supplied research, preserving intent and uncertainty."
)
TOOLS = ("read_sources", "read_context", "lookup_evidence")


class UnderstandingSubmission(Record):
    context: ResearchContext
    decisions: list[ResearcherDecision] = Field(default_factory=list)


def brief(ctx: ToolContext, **focus) -> str:
    return (
        "Read the original sources using tools. Submit a typed context with source-stated, "
        "agent-proposed or unknown annotations. Source-stated annotations require exact source_ref "
        "and source_span. Use claim names for claim annotations and JSON pointers for other "
        "scientific commitments, especially /analysis/model. Do not invent missing claims "
        "or mark proposals researcher-confirmed. "
        "Record contradictions and missing scientific decisions even before a design exists. "
        "Quantities from sources require a verbatim span supporting value and unit; otherwise "
        "mark an explicit planning assumption, or leave missing. Context package.sources will "
        "be populated from immutable sources. Bind each decision to this run and binding.\n"
        + json.dumps(
            {
                "run_id": ctx.run_id,
                "binding": ctx.binding.model_dump(),
                "context_revision": ctx.store.run(ctx.run_id).context_revision,
                "focus": focus,
            },
            ensure_ascii=False,
        )
    )


def submit_tool(ctx: ToolContext, **focus):
    def accept(payload: UnderstandingSubmission):
        context = payload.context
        binding = ctx.binding
        if context.source_snapshot_id != binding.source_snapshot_id:
            raise ValueError("source binding differs")
        if context.revision != (binding.context_revision or 0) + 1:
            raise ValueError("context revision differs")
        supplied = {s.id: s for s in sources(ctx)}
        previous = ctx.store.context(ctx.run_id)
        if previous:
            old_claim, new_claim = package_claim(previous.package), package_claim(context.package)
            for path, annotation in previous.field_annotations.items():
                if annotation.status == "researcher-confirmed" and (
                    context.field_annotations.get(path) != annotation
                    or (path in old_claim and old_claim[path] != new_claim[path])
                    or (
                        path.startswith("/context/")
                        and resolve(previous, path.removeprefix("/context"))
                        != resolve(context, path.removeprefix("/context"))
                    )
                    or (
                        path == "/analysis/model"
                        and previous.package.hypothesis.method != context.package.hypothesis.method
                    )
                ):
                    raise ValueError(f"researcher-confirmed intent cannot change: {path}")
        for path, annotation in context.field_annotations.items():
            if annotation.status == "researcher-confirmed" and (
                previous is None or previous.field_annotations.get(path) != annotation
            ):
                raise ValueError("agent cannot confer researcher-confirmed authority")
            if annotation.status == "source-stated":
                source = supplied.get(annotation.source_ref)
                if (
                    source is None
                    or not annotation.source_span
                    or annotation.source_span not in source.text
                ):
                    raise ValueError(f"invalid source span for {path}")
                if (
                    isinstance(annotation.value, (int, float))
                    and not isinstance(annotation.value, bool)
                    and not supports_number(annotation.value, annotation.source_span)
                ):
                    raise ValueError(f"source span does not support typed commitment {path}")
        if context.package.inference and context.package.inference.margin is not None:
            annotation = context.field_annotations.get("margin")
            if annotation is None:
                raise ValueError("margin needs authority annotation")
            if annotation.status == "source-stated" and not supports_number(
                context.package.inference.margin, annotation.source_span
            ):
                raise ValueError("source span does not support margin")
        for assumption in context.package.assumptions:
            quantity = assumption.quantity
            if quantity is None:
                continue
            if quantity.provenance == "computed":
                raise ValueError("unattested computed input is unsupported")
            if quantity.provenance == "source":
                source = supplied.get(quantity.source_ref)
                span = quantity.source_span
                if (
                    source is None
                    or not span
                    or span not in source.text
                    or not supports_number(quantity.value, span)
                    or (quantity.unit and quantity.unit.casefold() not in span.casefold())
                ):
                    raise ValueError("quantity source span does not support value/unit")
        for decision in payload.decisions:
            if decision.run_id != ctx.run_id or decision.binding != binding:
                raise ValueError("decision binding differs")
            if (
                decision.responses
                or decision.application
                or decision.status not in {"IDENTIFIED", "EXPLORING"}
            ):
                raise ValueError("agent cannot supply researcher responses or authorization")
        context.package.sources = list(supplied.values())
        return {
            "context": context.model_dump(mode="json"),
            "decisions": [d.model_dump(mode="json") for d in payload.decisions],
        }

    return submit_schema("submit_understanding", UnderstandingSubmission, accept)


def supports_number(value: float | None, span: str | None) -> bool:
    return (
        value is not None
        and span is not None
        and any(
            float(number) == value
            for number in re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", span)
        )
    )
