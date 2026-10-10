"""Read-only scientific tools and form validation shared by the four agents."""

import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field, ValidationError

from experiment_design.contracts import (
    CLAIM_FIELDS,
    Design,
    DesignVersion,
    Finding,
    Patch,
    Quantity,
    Record,
    ResearcherRequest,
    ScientificPackage,
)
from experiment_design.package import all_sources, intake_gaps
from experiment_design.patch import PatchError, apply_patch, design_diff, resolve
from experiment_design.runtime.loop import SubmitRejected, Tool
from experiment_design.runtime.store import Store
from experiment_design.science.evidence import lookup_evidence
from experiment_design.science.power import compute_scenario
from experiment_design.science.validators import validate_design


@dataclass
class ToolContext:
    store: Store
    run_id: str
    package: ScientificPackage
    version: DesignVersion | None
    accepted_call_ids: set[str]
    pending_findings: list[Finding] = field(default_factory=list)


class RequestProposal(Record):
    kind: Literal["decision", "input"]
    question: str
    path: str | None = None
    options: list[str] = Field(default_factory=list)


def sources(ctx: ToolContext):
    return all_sources(ctx.package, ctx.store.requests(ctx.run_id))


def source_ids(ctx: ToolContext) -> set[str]:
    return {s.id for s in sources(ctx)}


def validate_refs(refs: list[str], ctx: ToolContext) -> list[str]:
    known = source_ids(ctx) | {f"tool:{call_id}" for call_id in ctx.accepted_call_ids}
    return [f"Unknown evidence ref {ref}" for ref in refs if ref not in known]


def validate_findings(findings: list[Finding], ctx: ToolContext) -> list[str]:
    errors = []
    existing = {i.id for i in ctx.store.issues(ctx.run_id)}
    for finding in findings:
        if not finding.paths or not finding.mechanism.strip() or not finding.consequence.strip():
            errors.append("Findings need paths, mechanism and consequence")
        for path in finding.paths:
            try:
                if ctx.version is None:
                    raise PatchError(["No design to review"])
                resolve(ctx.version.design, path)
            except PatchError as exc:
                errors.extend(exc.errors)
        errors.extend(validate_refs(finding.evidence_refs, ctx))
        if finding.basis == "computation" and not any(
            ref.startswith("tool:") for ref in finding.evidence_refs
        ):
            errors.append("Computation basis requires a tool: evidence ref")
        if finding.duplicate_of is not None and finding.duplicate_of not in existing:
            errors.append(f"Unknown duplicate_of {finding.duplicate_of}")
    return errors


def requests(proposals: list[RequestProposal], ctx: ToolContext) -> list[dict]:
    result = []
    for proposal in proposals:
        if proposal.kind == "decision" and proposal.path not in {
            f"/claim/{field}" for field in CLAIM_FIELDS
        }:
            raise SubmitRejected(["Decision requests need a /claim/<field> path"])
        if not proposal.question.strip():
            raise SubmitRejected(["Researcher questions must be non-empty"])
        result.append(
            ResearcherRequest(
                **proposal.model_dump(),
                run_id=ctx.run_id,
                raised_by="design",
            ).model_dump(mode="json")
        )
    return result


def submit_schema(name: str, model: type[Record], handler) -> Tool:
    def validate(args):
        try:
            return handler(model.model_validate(args))
        except (ValidationError, PatchError, ValueError) as exc:
            raise SubmitRejected(
                getattr(exc, "errors", None) if isinstance(exc, PatchError) else [str(exc)]
            ) from exc

    return Tool(name, "Submit the validated task result", model.model_json_schema(), validate)


def context_brief(ctx: ToolContext) -> str:
    package = ctx.package.model_dump(mode="json", exclude={"sources", "assumptions"})
    ledger = [i.model_dump(mode="json") for i in ctx.store.issues(ctx.run_id)]
    return json.dumps(
        {
            "package": package,
            "intake_gaps": intake_gaps(ctx.package),
            "version": ctx.version.version if ctx.version else None,
            "issue_ledger": ledger,
            "pending_findings": [f.model_dump(mode="json") for f in ctx.pending_findings],
        },
        ensure_ascii=False,
    )


def build_tools(ctx: ToolContext) -> dict[str, Tool]:
    def current(args):
        if args.get("design") is not None:
            return Design.model_validate(args["design"])
        if ctx.version is None:
            raise ValueError("No committed design; pass the proposed design for preview")
        return ctx.version.design

    def candidate(args):
        d = current(args)
        if args.get("ops") is not None:
            d = apply_patch(
                d,
                Patch(ops=args["ops"], rationale="Read-only patch preview"),
                source_ids=source_ids(ctx),
            )
        return d

    def read_design(args):
        return resolve(current({}), args.get("path", "/"))

    def read_issues(args):
        selected = [
            i.model_dump(mode="json")
            for i in ctx.store.issues(ctx.run_id)
            if args.get("status") is None or i.status == args["status"]
        ]
        return selected + [
            f.model_dump(mode="json") | {"pending": True, "raised_by": "methodologist"}
            for f in ctx.pending_findings
        ]

    def validators(args):
        return [
            f.model_dump(mode="json")
            for f in validate_design(
                candidate(args),
                ctx.package,
                source_ids(ctx),
            )
        ]

    def compute(args):
        d = current(args)
        selected = d.scenarios
        if args.get("scenario") is not None:
            selected = [s for s in selected if s.name == args["scenario"]]
            if not selected:
                raise ValueError(f"Unknown scenario {args['scenario']}")
        overrides = {
            k: Quantity.model_validate(v) for k, v in (args.get("overrides") or {}).items()
        }
        return [compute_scenario(d, s, overrides).model_dump(mode="json") for s in selected]

    def check_patch(args):
        before = current({})
        after = candidate(args)
        return {
            "diff": design_diff(before, after),
            "findings": [
                f.model_dump(mode="json")
                for f in validate_design(
                    after,
                    ctx.package,
                    source_ids(ctx),
                )
            ],
        }

    def read_diff(args):
        before = ctx.store.version(ctx.run_id, args["from_version"])
        after = ctx.store.version(ctx.run_id, args["to_version"])
        if before is None or after is None:
            raise ValueError("Unknown version for diff")
        return design_diff(before.design, after.design)

    def tool(name, description, properties, required, handler):
        schema = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
        if "design" in properties:
            schema["$defs"] = definitions
        return Tool(name, description, schema, handler)

    string = {"type": "string"}
    ops = {"type": "array", "items": Patch.model_json_schema()["$defs"]["PatchOp"]}
    draft = Design.model_json_schema()
    definitions = draft.pop("$defs")
    # Attach the referenced scientific definitions to each preview schema.
    preview = {"design": draft}
    result = {
        "read_package": tool(
            "read_package",
            "Read researcher input",
            {},
            [],
            lambda _: {
                "package": ctx.package.model_dump(mode="json"),
                "researcher_inputs": [
                    s.model_dump(mode="json") for s in sources(ctx) if s.kind == "researcher_input"
                ],
            },
        ),
        "lookup_evidence": tool(
            "lookup_evidence",
            "Find source-backed excerpts",
            {"query": string},
            ["query"],
            lambda args: [
                h.model_dump(mode="json") for h in lookup_evidence(args["query"], sources(ctx))
            ],
        ),
        "read_design": tool(
            "read_design", "Read a design section", {"path": string}, [], read_design
        ),
        "read_issues": tool(
            "read_issues",
            "Read existing and pending findings",
            {"status": {"type": ["string", "null"]}},
            [],
            read_issues,
        ),
        "run_validators": tool(
            "run_validators",
            "Validate current or proposed design",
            preview | {"ops": ops},
            [],
            validators,
        ),
        "compute_power": tool(
            "compute_power",
            "Compute power with honest quantitative inputs",
            preview
            | {
                "scenario": {"type": ["string", "null"]},
                "overrides": {
                    "type": "object",
                    "additionalProperties": Quantity.model_json_schema(),
                },
            },
            [],
            compute,
        ),
        "check_patch": tool(
            "check_patch",
            "Preview a patch, diff and validation",
            {"ops": ops},
            ["ops"],
            check_patch,
        ),
        "read_diff": tool(
            "read_diff",
            "Read the actual before/after changes",
            {
                "from_version": {"type": "integer"},
                "to_version": {"type": "integer"},
            },
            ["from_version", "to_version"],
            read_diff,
        ),
    }
    return result
