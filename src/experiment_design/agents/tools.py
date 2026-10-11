"""Read-only scientific tools and form validation shared by the four agents."""

import json
from dataclasses import dataclass, field
from hashlib import sha256

from pydantic import Field, ValidationError

from experiment_design.contracts import (
    ArtifactBinding,
    CalculationRequest,
    DecisionOption,
    Design,
    DesignVersion,
    Finding,
    Patch,
    Quantity,
    Record,
    ResearcherDecision,
    ScientificPackage,
)
from experiment_design.package import all_sources, intake_gaps
from experiment_design.patch import (
    PatchError,
    apply_patch,
    design_diff,
    preview_design,
    quantities,
    resolve,
    scientific_commitments,
)
from experiment_design.runtime.loop import SubmitRejected, Tool
from experiment_design.runtime.store import Store
from experiment_design.science.evidence import lookup_evidence
from experiment_design.science.power import calculate, compute_scenario
from experiment_design.science.validators import validate_design


@dataclass
class ToolContext:
    store: Store
    run_id: str
    package: ScientificPackage | None
    version: DesignVersion | None
    accepted_call_ids: set[str]
    pending_findings: list[Finding] = field(default_factory=list)
    binding: ArtifactBinding | None = None
    issue_cutoff: int | None = None
    task_id: str | None = None
    cohort_id: str | None = None

    def __post_init__(self):
        if self.binding is None and self.store.run(self.run_id).schema_version == 2:
            self.binding = self.store.binding(self.run_id)


class DecisionProposal(Record):
    problem: str
    affected_paths: list[str]
    dependencies: list[str] = Field(default_factory=list)
    options: list[DecisionOption] = Field(default_factory=list)
    recommendation: str = ""


def sources(ctx: ToolContext):
    if ctx.store.run(ctx.run_id).schema_version == 2:
        from experiment_design.contracts import Source

        raw = [
            Source(id=a.ref, title=a.path, kind="note", text=a.text)
            for a in ctx.store.source_snapshot(ctx.run_id).artifacts
        ]
        context = ctx.store.context(ctx.run_id, ctx.binding.context_revision)
        decisions = ctx.store._artifact_records(
            ctx.run_id,
            "researcher_decision",
            ResearcherDecision,
            ctx.issue_cutoff if ctx.cohort_id else None,
        )
        supplied = all_sources(context, decisions) if context else []
        return list({s.id: s for s in supplied + raw}.values())
    return all_sources(ctx.package, ctx.store.requests(ctx.run_id))


def source_ids(ctx: ToolContext) -> set[str]:
    return {s.id for s in sources(ctx)}


def validate_refs(refs: list[str], ctx: ToolContext) -> list[str]:
    known = source_ids(ctx)
    for call_id in ctx.accepted_call_ids:
        if ctx.binding is None:
            known.add(f"tool:{call_id}")
            continue
        try:
            observation = ctx.store.observation(ctx.run_id, call_id)
        except KeyError:
            continue
        if (
            observation.accepted
            and observation.task_id == ctx.task_id
            and observation.binding == ctx.binding
        ):
            known.add(observation.evidence_ref)
    return [
        f"Unknown evidence ref {ref}. Use an exact reference from: {', '.join(sorted(known))}"
        for ref in refs
        if ref not in known
    ]


def validate_findings(findings: list[Finding], ctx: ToolContext) -> list[str]:
    errors = []
    existing = {i.id for i in visible_issues(ctx)}
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
        if (
            ctx.binding
            and finding.basis == "computation"
            and not computation_refs(ctx).intersection(finding.evidence_refs)
        ):
            errors.append(
                "Computation basis requires an accepted compute_power call from this task"
            )
        if finding.duplicate_of is not None and finding.duplicate_of not in existing:
            errors.append(f"Unknown duplicate_of {finding.duplicate_of}")
    return errors


def validate_source_quantities(design: Design | dict, ctx: ToolContext) -> None:
    if ctx.binding is None:
        return
    from experiment_design.agents.understanding import supports_number

    known = {s.id: s for s in sources(ctx)}
    data = design.model_dump(mode="json") if isinstance(design, Design) else design
    for path, quantity in quantities(data):
        if quantity["provenance"] != "source":
            continue
        source = known.get(quantity.get("source_ref"))
        span = quantity.get("source_span")
        if (
            source is None
            or not span
            or span not in source.text
            or not supports_number(quantity.get("value"), span)
            or quantity.get("unit")
            and quantity["unit"].casefold() not in span.casefold()
        ):
            raise ValueError(f"{path}: source span does not support quantity value and unit")


def computation_refs(ctx: ToolContext) -> set[str]:
    refs = set()
    for call_id in ctx.accepted_call_ids:
        try:
            observation = ctx.store.observation(ctx.run_id, call_id)
        except KeyError:
            continue
        if (
            observation.accepted
            and observation.tool_name == "compute_power"
            and observation.binding == ctx.binding
            and observation.task_id == ctx.task_id
        ):
            refs.add(observation.evidence_ref)
    return refs


def decision_proposals(proposals: list[DecisionProposal], ctx: ToolContext) -> list[dict]:
    result = []
    for proposal in proposals:
        if not proposal.problem.strip() or not proposal.affected_paths:
            raise SubmitRejected(["Decision needs a problem and affected scientific paths"])
        for path in proposal.affected_paths:
            if path.startswith("/context/package/"):
                continue
            if ctx.version is not None:
                resolve(ctx.version.design, path)
            else:
                resolve(Design(claim={}), path)
        if ctx.binding is None:
            raise SubmitRejected(["Scientific decisions require a stage-bound run"])
        digest = sha256(json.dumps(proposal.model_dump(), sort_keys=True).encode()).hexdigest()[:20]
        result.append(
            ResearcherDecision(
                id="decision:" + digest,
                run_id=ctx.run_id,
                binding=ctx.binding,
                **proposal.model_dump(),
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
    context = ctx.store.context(ctx.run_id, ctx.binding.context_revision) if ctx.binding else None
    ledger = [i.model_dump(mode="json") for i in visible_issues(ctx)]
    guidance = (
        "Tool observations contain result and evidence_ref. Copy evidence_ref exactly into "
        "evidence_refs or scenarios_checked, e.g. tool:call_abc. Source references are exact "
        "source IDs, never prose. Computation findings must cite an accepted compute_power "
        "observation from this task; pending reviewer references cannot be reused as your own. "
        "Accepted tool calls need not produce numerical results. Interpret each compute_power "
        "result using status, reasons and applicability: computed is quantitative evidence "
        "within its inputs, binding and counterfactual scope; not_computed is evidence of the "
        "stated computation/assessment limits, not a quantitative result. Distinguish missing "
        "evidence or assumptions from calculator capability limits. Unavailability alone "
        "neither proves nor disproves a scientific defect. Ground defects in mechanism and "
        "consequence; disclose unresolved researcher/downstream work. "
        "For sensitivity use the committed design with overrides such as "
        '{"icc":{"value":0.2,"unit":"","provenance":"assumption"}}. '
        "Override keys are effect, sd, p_control, p_treatment, size and icc; "
        "they are names, not design paths. Preserve the canonical claim and outcome units.\n"
    )
    return guidance + json.dumps(
        {
            "package": package,
            "scientific_commitments": scientific_commitments(context) if context else {},
            "intake_gaps": intake_gaps(ctx.package),
            "version": ctx.version.version if ctx.version else None,
            "issue_ledger": ledger,
            "pending_findings": []
            if ctx.cohort_id
            else [f.model_dump(mode="json") for f in ctx.pending_findings],
            "source_ids": sorted(source_ids(ctx)),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def visible_issues(ctx: ToolContext):
    if ctx.issue_cutoff is None:
        return ctx.store.issues(ctx.run_id)
    issues = {}
    for e in ctx.store.events(ctx.run_id):
        if e.event_id > ctx.issue_cutoff:
            break
        if e.type == "issue" and "record" in e.data:
            from experiment_design.contracts import ReviewIssue

            issues[e.data["id"]] = ReviewIssue.model_validate(e.data["record"])
    return list(issues.values())


def build_tools(ctx: ToolContext, *, compact_preview: bool = True) -> dict[str, Tool]:
    def current(args):
        if args.get("design") is not None:
            if ctx.version is not None:
                raise ValueError("Use the committed design; supply overrides or patch operations")
            return Design.model_validate(args["design"])
        if ctx.version is None:
            raise ValueError("No committed design; pass the proposed design for preview")
        return ctx.version.design

    def candidate(args):
        d = current(args)
        if args.get("ops") is not None:
            context = (
                ctx.store.context(ctx.run_id, ctx.binding.context_revision)
                if ctx.binding
                else ctx.store.context(ctx.run_id)
            )
            preview = preview_design if context else apply_patch
            d = preview(
                d,
                Patch(ops=args["ops"], rationale="Read-only patch preview"),
                source_ids=source_ids(ctx),
                **({"context": context} if context else {}),
            )
        validate_source_quantities(d, ctx)
        return d

    def read_design(args):
        return resolve(current({}), args.get("path", "/"))

    def read_issues(args):
        selected = [
            i.model_dump(mode="json")
            for i in visible_issues(ctx)
            if args.get("status") is None or i.status == args["status"]
        ]
        return selected + [
            f.model_dump(mode="json") | {"pending": True, "raised_by": "methodologist"}
            for f in ([] if ctx.cohort_id else ctx.pending_findings)
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
        candidate(args)
        selected = d.scenarios
        if args.get("scenario") is not None:
            selected = [s for s in selected if s.name == args["scenario"]]
            if not selected:
                raise ValueError(f"Unknown scenario {args['scenario']}")
        overrides = {
            k: Quantity.model_validate(v) for k, v in (args.get("overrides") or {}).items()
        }
        validate_source_quantities(
            {k: q.model_dump(mode="json") for k, q in overrides.items()}, ctx
        )
        if ctx.binding:
            return [
                calculate(
                    CalculationRequest(
                        binding=ctx.binding,
                        scenario_id=s.name,
                        design=d,
                        overrides=overrides,
                        preview_patch=Patch(ops=args["ops"], rationale="Read-only power preview")
                        if args.get("ops") is not None
                        else None,
                    )
                ).model_dump(mode="json")
                for s in selected
            ]
        previewed = candidate(args)
        return [
            compute_scenario(previewed, s, overrides).model_dump(mode="json")
            for s in previewed.scenarios
            if args.get("scenario") in {None, s.name}
        ]

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
        if "design" in properties and not compact_preview:
            schema["$defs"] = definitions
        return Tool(name, description, schema, handler)

    string = {"type": "string"}
    ops = {"type": "array", "items": Patch.model_json_schema()["$defs"]["PatchOp"]}
    preview, definitions = {}, {}
    if ctx.version is None:
        preview = {
            "design": {
                "type": "object",
                "description": "Proposed Design using the full schema in submit_design. "
                "The same Design and quantitative provenance validation applies to previews.",
            }
        }
        if not compact_preview:
            draft = Design.model_json_schema()
            definitions = draft.pop("$defs")
            preview = {"design": draft}
    result = {
        "read_sources": tool(
            "read_sources",
            "Read exact immutable supplied sources",
            {},
            [],
            lambda _: ctx.store.source_snapshot(ctx.run_id).model_dump(mode="json"),
        ),
        "read_context": tool(
            "read_context",
            "Read research understanding and authority",
            {},
            [],
            lambda _: (
                bound_context.model_dump(mode="json")
                if (
                    bound_context := ctx.store.context(ctx.run_id, ctx.binding.context_revision)
                    if ctx.binding
                    else ctx.store.context(ctx.run_id)
                )
                else None
            ),
        ),
        "read_package": tool(
            "read_package",
            "Read researcher input",
            {},
            [],
            lambda _: {
                "package": ctx.package.model_dump(mode="json") if ctx.package else None,
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
            "Compute power from the current design; before the first version supply design. "
            "Sensitivity overrides use effect, sd, p_control, p_treatment, size or icc, "
            "not design paths. Supply a Quantity with value, unit and provenance; "
            "effect/sd use outcome units, size uses the canonical cluster-size unit, "
            "icc uses an empty unit. "
            "Cite the returned evidence_ref when submitting computation findings.",
            preview
            | {
                "ops": ops,
                "scenario": {"type": ["string", "null"]},
                "overrides": {
                    "type": "object",
                    "properties": {
                        name: {"$ref": "#/$defs/Quantity"}
                        for name in ("effect", "sd", "p_control", "p_treatment", "size", "icc")
                    },
                    "additionalProperties": False,
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
    result["compute_power"].parameters.setdefault("$defs", {})["Quantity"] = (
        Quantity.model_json_schema()
    )
    return result
