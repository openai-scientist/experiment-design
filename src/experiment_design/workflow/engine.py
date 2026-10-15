"""Seven direct stages over immutable inputs and reviewed Markdown."""

import difflib
import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from experiment_design.agents.benchmark.github import GitHubClient
from experiment_design.agents.benchmark.hf import HFClient
from experiment_design.agents.benchmark.step import STEP_KEY as RESOURCES_STEP
from experiment_design.agents.benchmark.step import run_resources
from experiment_design.agents.benchmark.zenodo import ZenodoClient
from experiment_design.agents.prompts import snapshot
from experiment_design.agents.tools import run_specialist
from experiment_design.api.contract_models import EXD_RESOURCES
from experiment_design.config import ExdConfig, RunConfig
from experiment_design.contracts import (
    AgentOutput,
    Finding,
    ReviewerError,
    RunInterrupted,
    RunState,
    WorkflowError,
)
from experiment_design.packs import load_pack, validators
from experiment_design.packs.infer import infer_research_type
from experiment_design.persistence.api_state import read_run_meta
from experiment_design.persistence.store import RunStore
from experiment_design.publication.experiment_index import (
    ExperimentIndex,
    check_index,
    extract_index,
    rebind_index,
)
from experiment_design.runtime.jev import Judge, Question
from experiment_design.runtime.llm import LLMClient
from experiment_design.vocabulary import REVIEW_ROLES
from experiment_design.workflow.convergence import (
    converge_final,
    due_later,
    previous_majors,
    severity_adjustments,
)

# The input may hold several hypotheses; the path is unchanged so old runs resume.
HYPOTHESES_INPUT = "stage-01/hypothesis.md"
CRITICS = ("methodologist", "skeptic")


def start_run(inputs: list[Path], config: ExdConfig) -> RunStore:
    settings = config.model_dump(mode="json")
    store = RunStore.create(config.runs_root, inputs, settings, snapshot())
    store.update(
        llm_token_limit=config.run.llm_tokens,
        profile=config.profile if "profile" in config.model_fields_set else None,
    )
    return store


def _peer_critic_task(store: RunStore, task: str) -> bool:
    role, attempt, stage = task.split(":")[:3]
    return (
        role in {"methodologist", "skeptic"}
        and attempt == str(store.state.attempt)
        and stage == "4"
    )


def _inputs(
    store: RunStore, *, critic_round: bool = False, exclude_roles: frozenset[str] = frozenset()
) -> dict[str, str]:
    result = dict(store.sources())
    replies = [e["data"] for e in store.events() if e["kind"] == "researcher.response"]
    result["researcher_responses"] = json.dumps(replies, ensure_ascii=False, indent=2)
    accepted_tasks = {e["data"]["task"] for e in store.events() if e["kind"] == "agent.accepted"}
    calculations = [
        e["data"]
        for e in store.events()
        if e["kind"] == "calculation"
        and e["data"].get("task") in accepted_tasks
        and e["data"]["result"].get("status") == "computed"
        and not (critic_round and _peer_critic_task(store, e["data"]["task"]))
        and e["data"]["task"].split(":")[0] not in exclude_roles
    ]
    result["prior_accepted_calculations"] = json.dumps(calculations, ensure_ascii=False, indent=2)
    return result


def _with_replies(inputs: dict[str, str], *names: str) -> list[str]:
    """Required documents: the given names plus the replies document when it holds any."""
    return [*names, *(["researcher_responses"] if inputs["researcher_responses"] != "[]" else [])]


def _metadata(output: AgentOutput) -> str:
    return output.model_dump_json(exclude={"markdown"})


def _load(store: RunStore, stage: int, name: str) -> AgentOutput:
    return AgentOutput(
        markdown=store.read(stage, name + ".md"), **json.loads(store.read(stage, name + ".json"))
    )


def _agent(
    store: RunStore,
    llm: LLMClient,
    cfg: RunConfig,
    role: str,
    stage: int,
    name: str,
    workspace: dict[str, str],
    required: list[str],
    *,
    review_llm: LLMClient | None = None,
) -> AgentOutput:
    kind = store.interrupt()
    if kind:
        raise RunInterrupted(kind)
    store.event("agent.started", {"role": role, "stage": stage})
    client = review_llm if role in REVIEW_ROLES and review_llm is not None else llm
    output = run_specialist(
        role,
        store,
        client,
        cfg,
        workspace,
        required,
        pack=load_pack(store.state.profile) if store.state.profile is not None else None,
    )
    if role in REVIEW_ROLES:
        due_later(output)
    store.write(stage, name + ".md", output.markdown)
    store.write(stage, name + ".json", _metadata(output))
    return output


def _route(
    store: RunStore, judge: Judge, cfg: RunConfig, options: list[str], outputs: list[AgentOutput]
) -> str:
    if len(options) == 1:
        store.event("route", {"choice": options[0], "automatic": True})
        return options[0]
    if store.state.jev_calls >= cfg.jev_max_calls:
        raise WorkflowError("JEV call allowance exhausted")
    remaining = cfg.jev_max_calls - store.state.jev_calls
    if hasattr(judge, "max_attempts"):
        judge.max_attempts = min(3, remaining)
    question = Question(
        "Choose the next scientific action from the legal options.", {v: v for v in options}
    )
    failure = None
    decisions = {}
    try:
        decisions = judge.decide(
            {"stage": store.state.stage, "outputs": [o.model_dump() for o in outputs]},
            {"next": question},
        )
    except Exception as exc:
        failure = exc
    attempts = getattr(judge, "last_attempts", None)
    if not attempts:
        decision = decisions.get("next")
        tokens = decision.tokens if decision else None
        attempts = [
            {
                "status": "error" if failure else "success",
                "tokens_in": tokens,
                "tokens_out": 0 if tokens is not None else None,
            }
        ]
    known_tokens = 0
    invalid_usage = False
    for attempt in attempts:
        store.event("provider.usage", {"provider": "jev", **attempt})
        counts = [attempt.get("tokens_in"), attempt.get("tokens_out")]
        invalid_usage |= any(
            value is not None
            and (not isinstance(value, int) or isinstance(value, bool) or value < 0)
            for value in counts
        )
        if all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in counts
        ):
            known_tokens += sum(counts)
    store.update(
        jev_calls=store.state.jev_calls + len(attempts),
        llm_tokens=store.state.llm_tokens + known_tokens,
    )
    if invalid_usage:
        raise WorkflowError("JEV returned invalid token usage")
    if store.state.jev_calls > cfg.jev_max_calls:
        raise WorkflowError("JEV call allowance exceeded")
    if failure:
        raise WorkflowError("JEV provider failed") from failure
    decision = decisions.get("next")
    if decision is None or decision.choice not in options:
        raise WorkflowError("JEV returned a missing or illegal choice")
    if store.state.llm_tokens > store.state.llm_token_limit:
        raise WorkflowError("Run token allowance exceeded")
    store.event("route", {"choice": decision.choice, "automatic": False})
    return decision.choice


def _ask(store: RunStore, outputs: list[AgentOutput]) -> None:
    questions = [o.question for o in outputs if o.question.strip()]
    if not questions:
        raise WorkflowError("ASK_RESEARCHER requires a concrete agent question")
    asking = [o for o in outputs if o.question.strip()]
    legacy_combined = len(asking) > 1 and not read_run_meta(store.path).get("platform_run_id")
    store.update(
        status="AWAITING_RESEARCHER",
        question="\n".join(questions) if legacy_combined else asking[0].question,
        question_kind="ANSWER",
        question_options=[] if legacy_combined else asking[0].options,
        question_default=asking[0].default,
    )


def _ask_review(
    store: RunStore, *, step_key: str, stage: int, name: str, schema_id: str, prompt: str
) -> None:
    store.event(
        "review.requested",
        {
            "step_key": step_key,
            "stage": stage,
            "name": name,
            "schema_id": schema_id,
            "prompt": prompt,
            "attempt": store.state.attempt,
        },
    )
    store.update(
        status="AWAITING_RESEARCHER", question=prompt, question_kind="REVIEW", question_options=[]
    )


def secondary_review(store: RunStore, step_key: str) -> dict | None:
    result = None
    for event in store.events():
        data = event["data"]
        if event["kind"] == "review.requested" and data["step_key"] == step_key:
            result = None
        elif (
            event["kind"] == "researcher.response"
            and data.get("review_of") == step_key
            and data["revision"] == store.state.attempt
        ):
            result = data
    return result


# Specialist runs a path still needs, so the fresh final check always keeps its allowance.
_REFINE_TASKS = {
    "REVISE": 4,  # revision, two rejoinders, final check
    "REFRAME": 5,  # framing, design, two critics, final check
    "REQUEST_CHANGES": 6,  # two critics, revision, two rejoinders, final check
}
# Profiled runs also call the indexer, and a reframe reruns the surveyor and selector.
_PROFILED_EXTRA_TASKS = {"REVISE": 1, "REFRAME": 3, "REQUEST_CHANGES": 1}


def _refine_reserve(path: str, task_max_tokens: int, *, profiled: bool = False) -> int:
    return (_REFINE_TASKS[path] + profiled * _PROFILED_EXTRA_TASKS[path]) * task_max_tokens


def _resources_gate(
    store: RunStore, llm: LLMClient, cfg: RunConfig, integrations: Mapping[str, str] | None
) -> bool:
    """Run the resources step and its researcher review; True while the gate is open."""
    decision = secondary_review(store, RESOURCES_STEP)
    exists = (store.path / "stage-03" / "resources.json").is_file()
    if exists and decision is not None and decision["action"] == "APPROVE":
        return False
    if not exists or decision is not None:
        store.event("step.entered", {"step_key": RESOURCES_STEP})
        hf = HFClient(token=(integrations or {}).get("huggingface"))
        github = GitHubClient(token=os.environ.get("GITHUB_TOKEN"))
        zenodo = ZenodoClient()
        try:
            run_resources(
                store,
                llm,
                cfg,
                load_pack(store.state.profile),
                hf,
                github=github,
                zenodo=zenodo,
                comments=decision["text"] if decision is not None else "",
            )
        finally:
            hf.close()
            github.close()
            zenodo.close()
    _ask_review(
        store,
        step_key=RESOURCES_STEP,
        stage=3,
        name="resources.json",
        schema_id=EXD_RESOURCES,
        prompt="Review the proposed datasets, benchmarks and baselines before the design starts.",
    )
    return True


def _index_plan(store: RunStore, llm: LLMClient, cfg: RunConfig, stage: int, document: str) -> None:
    index = rebind_index(
        extract_index(store, llm, cfg, document, store.read(1, "hypothesis.md")), document
    )
    if store.state.profile and store.state.profile.research_type:
        for experiment in index.experiments:
            if experiment.type == "base":
                experiment.type = store.state.profile.research_type
    store.write(stage, "index.json", index.model_dump_json(by_alias=True, indent=2))


def _resource_notes(store: RunStore, document: str) -> str:
    try:
        resources = json.loads(store.read(3, "resources.json"))
    except FileNotFoundError:
        return document
    missing = [s for s in resources.get("shortfalls", []) if s not in document]
    return document + (
        "\n\n## Limitations\n" + "\n".join(f"- {s}" for s in missing) + "\n" if missing else ""
    )


def _deterministic_findings(
    store: RunStore, stage: int | None = None, plan_name: str | None = None
) -> list[Finding]:
    stage = stage or store.state.plan_stage
    plan = store.read(stage, plan_name or store.state.plan_name)
    # Heading and hash mismatches are mechanical: code repairs them before any check.
    index = rebind_index(ExperimentIndex.model_validate_json(store.read(stage, "index.json")), plan)
    try:
        resources = json.loads(store.read(3, "resources.json"))
    except FileNotFoundError:
        resources = {}
    ids = re.findall(r"^## (H\d+)\b", store.read(1, "hypothesis.md"), re.M)
    findings = check_index(index, plan, ids) + validators.run(
        load_pack(store.state.profile), index, resources
    )
    for i, shortfall in enumerate(resources.get("shortfalls", [])):
        findings.append(
            Finding(
                id=f"shortfall-{i}",
                severity="MINOR",
                category="baseline",
                anchor="Limitations",
                issue=shortfall,
                consequence="Resource minimum is not established",
                suggestion="Disclose the shortfall",
                kind="needs_evidence",
            )
        )
    store.activity(
        "exd.index.checked",
        actor="validator",
        summary=f"Index checked: {len(findings)} findings",
        payload={"count": len(findings)},
    )
    return findings


def _can_refine(store: RunStore, cfg: RunConfig, path: str) -> bool:
    return (
        store.state.refinements < cfg.max_refinements
        and store.state.llm_token_limit - store.state.llm_tokens
        >= _refine_reserve(path, cfg.task_max_tokens, profiled=store.state.profile is not None)
    )


_LIMITS = ("jev_max_calls", "max_refinements", "task_max_turns", "task_max_tokens")


def pin_limits(store: RunStore, run: RunConfig) -> None:
    """Persist changed run limits, with a journal entry, before the run advances.

    The token ceiling is not a pinned limit here: only an explicit budget increase changes it.
    """
    with store.execution():
        persisted = (store.config().get("run")) or {}
        new = {name: getattr(run, name) for name in _LIMITS}
        old = {name: persisted.get(name) for name in _LIMITS}
        if old != new:
            store.event("limits.changed", {"old": old, "new": new})
            store.set_run_limits(new)


def _forced_stop(store: RunStore, stage: int) -> bool:
    """True when a STOP was the only legal option because limits excluded every refinement."""
    route = json.loads(store.read(stage, "route.json"))
    return route["choice"] == "STOP" and route.get("forced") is True


def _major_fix(finding: Finding) -> bool:
    return finding.severity == "MAJOR" and finding.kind == "fix"


def _open_evidence(final: AgentOutput, maintained: list[AgentOutput]) -> str:
    """Limitations section for MAJOR findings that only evidence or a lookup can resolve."""
    items = [
        f"- {f.issue}"
        for o in [final, *maintained]
        for f in o.findings
        if f.severity == "MAJOR" and f.kind == "needs_evidence"
    ]
    if not items:
        return ""
    return "\n\n## Limitations (open, need evidence)\n" + "\n".join(items) + "\n"


def _critic_options(
    store: RunStore,
    cfg: RunConfig,
    method: AgentOutput,
    skeptic: AgentOutput,
    changes_requested: bool,
    deterministic: Sequence[Finding] = (),
) -> tuple[list[str], bool]:
    """Legal routes after the critics, and whether STOP stands alone only because of limits."""
    # A code check on the draft calls for a revision only when one is left; otherwise the
    # final check reports it as before.
    material = any(_major_fix(f) for o in [method, skeptic] for f in o.findings) or (
        any(_major_fix(f) for f in deterministic) and _can_refine(store, cfg, "REVISE")
    )
    options = (
        []
        if material or changes_requested or method.needs_input or skeptic.needs_input
        else ["CHECK"]
    )
    paths = ["REVISE"] if changes_requested else ["REVISE", "REFRAME"]
    # A revision is offered only for an open MAJOR fix finding (or a researcher's request).
    revisable = material or changes_requested
    available = [path for path in paths if revisable and _can_refine(store, cfg, path)]
    if changes_requested and available:
        # The researcher asked for changes: the judge does not overrule them (live: the judge
        # chose STOP after three change requests, so none was ever revised).
        return available, False
    options += available
    if method.question.strip() or skeptic.question.strip():
        options.append("ASK_RESEARCHER")
    # The first critic round revises a fixable MAJOR rather than stopping on it (live: the judge
    # chose STOP over an undeclared seed count and the plan shipped with it open). Later rounds
    # leave STOP to the judge, so one forced revision per check is the most this rule costs.
    if not (material and available and store.state.refinements == 0):
        options.append("STOP")
    return options, options == ["STOP"] and revisable and available != paths


def _final_review_names(store: RunStore) -> list[str]:
    optional = (
        "final_objections",
        "plan_diff",
        "revision_responses",
        "methodologist_rejoinder",
        "skeptic_rejoinder",
    )
    return ["methodologist_review", "skeptic_review", "response"] + [
        name for name in optional if (store.path / "stage-04" / (name + ".md")).is_file()
    ]


def _maintained(store: RunStore, review_names: list[str]) -> list[AgentOutput]:
    return [
        _load(store, 4, name)
        for name in ("methodologist_rejoinder", "skeptic_rejoinder")
        if name in review_names
    ]


def _final_options(
    store: RunStore,
    cfg: RunConfig,
    final: AgentOutput,
    maintained: list[AgentOutput],
    first_check: bool = True,
) -> tuple[list[str], bool]:
    fixable = any(_major_fix(f) for o in [final, *maintained] for f in o.findings)
    material = final.needs_input or fixable
    options = [] if material else ["PRESENT"]
    paths = ["REVISE", "REFRAME"]
    # MAJOR needs_evidence findings alone do not call for a revision: the plan is presented
    # with them listed under Limitations.
    available = [path for path in paths if fixable and _can_refine(store, cfg, path)]
    options += available
    if final.question.strip():
        options.append("ASK_RESEARCHER")
    # The first final check revises a fixable MAJOR it finds rather than stopping on it.
    if not (fixable and available and first_check):
        options.append("STOP")
    return options, options == ["STOP"] and fixable and available != paths


def _previous_final(store: RunStore) -> tuple[list[Finding], str] | None:
    """The findings and plan of the most recent archived final check, if any."""
    root = store.path / "attempts"
    numbers = sorted((int(d.name) for d in root.glob("*") if d.name.isdigit()), reverse=True)
    for number in numbers:
        archive = root / str(number)
        try:
            raw = json.loads(
                (archive / "stage-05" / "final_review.json").read_text(encoding="utf-8")
            )
            plan = (archive / "stage-04" / "revised_plan.md").read_text(encoding="utf-8")
            return [Finding.model_validate(f) for f in raw["findings"]], plan
        except (OSError, ValueError, KeyError):
            continue
    return None


def _plan(store: RunStore, stage: int, name: str) -> None:
    text = store.read(stage, name)
    store.update(
        plan_stage=stage,
        plan_name=name,
        plan_hash=hashlib.sha256(text.encode()).hexdigest(),
        review_complete=False,
        approval="pending",
    )


def _reframe(store: RunStore, objections: dict[str, str]) -> None:
    """Persist the objections before archiving, so a stop cannot lose why we reframe."""
    store.event("reframe.objections", {"attempt": store.state.attempt, "objections": objections})
    store.update(refinements=store.state.refinements + 1)
    store.archive_from(2)


def _reframe_inputs(store: RunStore) -> dict[str, str]:
    recorded = [e["data"] for e in store.events() if e["kind"] == "reframe.objections"]
    if not recorded:
        return {}
    return {"reframe_objections": json.dumps(recorded[-1]["objections"], ensure_ascii=False)}


def _changes_requested(store: RunStore) -> bool:
    """True while the latest researcher change request is the one this attempt is serving."""
    replies = [e["data"] for e in store.events() if e["kind"] == "researcher.response"]
    return bool(
        replies
        and replies[-1]["action"] == "REQUEST_CHANGES"
        and replies[-1]["revision"] + 1 == store.state.attempt
    )


def _pending_revision(store: RunStore) -> dict | None:
    """The engine-chosen revision archived by the previous attempt and not yet completed."""
    recorded = [e["data"] for e in store.events() if e["kind"] == "revision.pending"]
    if recorded and recorded[-1]["attempt"] + 1 == store.state.attempt:
        return recorded[-1]
    return None


def _answer_revision(store: RunStore) -> dict | None:
    """The revision an ANSWER to an S4/S5 question resumes, so the reply reaches the plan
    without re-running framing, design and the critics; None means restart from framing."""
    stage = store.state.stage
    if stage == 5 and store.valid(5) and store.state.plan_stage is not None:
        return {
            "plan": store.read(store.state.plan_stage, store.state.plan_name),
            "objections": {
                "methodologist_review": store.read(4, "methodologist_review.md"),
                "skeptic_review": store.read(4, "skeptic_review.md"),
                "final_objections": store.read(5, "final_review.md"),
            },
        }
    if stage != 4 or not store.valid(4):
        return None
    if (store.path / "stage-04" / "methodologist_review.json").is_file():
        if store.state.plan_stage is None:
            return None
        return {
            "plan": store.read(store.state.plan_stage, store.state.plan_name),
            "objections": {
                name: store.read(4, name + ".md")
                for name in ("methodologist_review", "skeptic_review")
            },
        }
    recorded = [e["data"] for e in store.events() if e["kind"] == "revision.pending"]
    if recorded and recorded[-1]["attempt"] + 1 == store.state.attempt:
        return {"plan": recorded[-1]["plan"], "objections": recorded[-1]["objections"]}
    return None


def _revision_input_plan(store: RunStore) -> str:
    if store.state.plan_stage is not None:
        old_plan = store.read(store.state.plan_stage, store.state.plan_name)
    else:
        requests = [
            e["data"]
            for e in store.events()
            if e["kind"] == "researcher.response" and e["data"].get("action") == "REQUEST_CHANGES"
        ]
        if requests:
            request = requests[-1]
            previous = (
                store.path / "attempts" / str(request["revision"]) / "stage-04" / "revised_plan.md"
            )
            old_plan = previous.read_bytes().decode("utf-8")
            if hashlib.sha256(old_plan.encode()).hexdigest() != request["plan_hash"]:
                raise WorkflowError("Requested plan identity is stale")
        else:
            old_plan = store.read(3, "draft_plan.md")
    return old_plan


def _revision(
    store: RunStore,
    llm: LLMClient,
    cfg: RunConfig,
    objections: dict[str, str],
    review_llm: LLMClient | None = None,
) -> None:
    old_plan = _revision_input_plan(store)
    objections = _with_finding_records(store, objections)
    store.event(
        "revision.pending",
        {"attempt": store.state.attempt, "plan": old_plan, "objections": objections},
    )
    store.update(refinements=store.state.refinements + 1)
    store.archive_from(4)
    _run_revision(store, llm, cfg, old_plan, objections, review_llm)


def _with_finding_records(store: RunStore, objections: dict[str, str]) -> dict[str, str]:
    records = []
    for stage, name in (
        (4, "methodologist_review.json"),
        (4, "skeptic_review.json"),
        (5, "final_review.json"),
    ):
        try:
            found = store.bound_findings(stage, name, store.state.attempt)
        except FileNotFoundError:
            continue
        # The reviser answers by finding_id; a raw reviewer id (F1 from both critics) misleads it.
        records.extend({k: v for k, v in f.items() if k != "id"} for f in found)
    return objections | {"revision_findings": json.dumps(records, ensure_ascii=False)}


def _plan_diff(old_plan: str, revised_plan: str) -> str:
    lines = difflib.unified_diff(
        old_plan.splitlines(),
        revised_plan.splitlines(),
        fromfile="previous_plan",
        tofile="revised_plan",
        lineterm="",
    )
    return (
        "\n".join(lines) + "\n"
        if old_plan != revised_plan
        else ("No differences between the previous and revised plan.\n")
    )


def _response(output: AgentOutput) -> str:
    """The designer's disposition of the objections: summary, then each finding."""
    parts = [output.recommended_action] if output.recommended_action else []
    if output.findings:
        parts.append(
            "\n".join(
                f"- {f.id} ({f.severity}): {f.issue}\n  Disposition: {f.suggestion}"
                for f in output.findings
            )
        )
    return "\n\n".join(parts) or (
        "No separate disposition supplied; the final checker must assess every objection."
    )


def _rejoinders(
    store: RunStore,
    llm: LLMClient,
    cfg: RunConfig,
    objections: dict[str, str],
    review_llm: LLMClient | None = None,
):
    """Each critic answers the designer's response; an accepted rejoinder is never re-run."""
    for critic in CRITICS:
        name = critic + "_rejoinder"
        if (store.path / "stage-04" / (name + ".json")).is_file():
            continue
        peer = next(c for c in CRITICS if c != critic)
        own = {k: v for k, v in objections.items() if k in {critic + "_review", "final_objections"}}
        if not own:
            raise WorkflowError(f"No objection document for the {critic} rejoinder")
        workspace = (
            _inputs(store, exclude_roles=frozenset({peer, peer + "_rejoinder"}))
            | {
                "research_brief": store.read(2, "research_brief.md"),
                "plan": store.read(4, "revised_plan.md"),
                "plan_diff": store.read(4, "plan_diff.md"),
                "response": store.read(4, "response.md"),
                "revision_responses": store.read(4, "revision_responses.md"),
            }
            | own
        )
        required = [HYPOTHESES_INPUT, "plan", "plan_diff", "response", *own]
        _agent(store, llm, cfg, name, 4, name, workspace, required, review_llm=review_llm)


def _run_revision(
    store: RunStore,
    llm: LLMClient,
    cfg: RunConfig,
    old_plan: str,
    objections: dict[str, str],
    review_llm: LLMClient | None = None,
) -> None:
    store.update(stage=4)
    # route.json is written last, so a resumed run never replays an accepted revision.
    if not (store.path / "stage-04" / "route.json").is_file():
        workspace = (
            _inputs(store)
            | {"research_brief": store.read(2, "research_brief.md"), "plan": old_plan}
            | objections
        )
        output = _agent(
            store,
            llm,
            cfg,
            "revision",
            4,
            "revised_plan",
            workspace,
            _with_replies(workspace, HYPOTHESES_INPUT, "research_brief", "plan", *objections),
        )
        if store.state.profile is not None:
            output.markdown = _resource_notes(store, output.markdown)
            store.write(4, "revised_plan.md", output.markdown)
            _index_plan(store, llm, cfg, 4, output.markdown)
        for name, text in objections.items():
            store.write(4, name + ".md", text)
        store.write(4, "response.md", _response(output))
        store.write(
            4,
            "revision_responses.md",
            json.dumps([r.model_dump() for r in output.responses], ensure_ascii=False, indent=2),
        )
        store.write(4, "plan_diff.md", _plan_diff(old_plan, output.markdown))
        asking = output.needs_input and bool(output.question.strip())
        store.write(
            4, "route.json", json.dumps({"choice": "ASK_RESEARCHER" if asking else "CHECK"})
        )
    outputs = [
        "revised_plan.md",
        "revised_plan.json",
        "response.md",
        "revision_responses.md",
        "plan_diff.md",
        "route.json",
    ] + [name + ".md" for name in objections]
    if store.state.profile is not None:
        outputs.append("index.json")
    asking = json.loads(store.read(4, "route.json"))["choice"] == "ASK_RESEARCHER"
    if not asking:
        _rejoinders(store, llm, cfg, objections, review_llm)
        outputs += [
            "methodologist_rejoinder.md",
            "methodologist_rejoinder.json",
            "skeptic_rejoinder.md",
            "skeptic_rejoinder.json",
        ]
    dependencies = [(1, "hypothesis.md"), (2, "research_brief.md"), (3, "draft_plan.md")]
    store.complete(4, dependencies, outputs)
    _plan(store, 4, "revised_plan.md")
    if not any(
        e["kind"] == "revision.completed" and e["data"]["attempt"] == store.state.attempt
        for e in store.events()
    ):
        metadata = json.loads(store.read(4, "revised_plan.json"))
        store.event(
            "revision.completed",
            {
                "attempt": store.state.attempt,
                "round": max(1, store.state.attempt - 1),
                "before": old_plan,
                "after": store.read(4, "revised_plan.md"),
                "patch": store.read(4, "plan_diff.md"),
                "responses": metadata.get("responses", []),
                "findings": json.loads(objections.get("revision_findings", "[]")),
            },
        )
    if not asking:
        store.update(stage=5)


def advance(
    store: RunStore,
    llm: LLMClient,
    judge: Judge,
    cfg: RunConfig,
    *,
    review_llm: LLMClient | None = None,
    hold_at_review: bool = False,
    integrations: Mapping[str, str] | None = None,
) -> RunState:
    with store.execution():
        stopped = {"USAGE_UNKNOWN", "REJECTED", "AWAITING_RESEARCHER", "CANCELLED"}
        if hold_at_review and store.state.approval != "approved":
            stopped.add("PENDING_REVIEW")
        if store.state.usage_unknown or store.state.status in stopped:
            return store.state
        try:
            return _advance(store, llm, judge, cfg, review_llm, hold_at_review, integrations)
        except ReviewerError:
            if not hold_at_review:
                store.update(status="STOPPED")
                raise
            if store.state.plan_stage is None:
                # A re-review failed before a plan was current: there is nothing to present.
                store.update(status="STOPPED")
                store.event("workflow.stop", {"reason": "Reviewer failed after two retries"})
                raise
            store.event(
                "review.error",
                {"attempt": store.state.attempt, "error": "Reviewer failed after two retries"},
            )
            return store.update(status="REVIEW_ERROR", review_complete=False)
        except WorkflowError as exc:
            if store.state.status != "USAGE_UNKNOWN":
                store.update(status="STOPPED")
            store.event("workflow.stop", {"reason": str(exc)})
            raise


def _advance(
    store: RunStore,
    llm: LLMClient,
    judge: Judge,
    cfg: RunConfig,
    review_llm: LLMClient | None = None,
    hold_at_review: bool = False,
    integrations: Mapping[str, str] | None = None,
) -> RunState:
    from experiment_design.publication.plan import publish

    if store.state.question_kind == "CLARIFY":
        question = store.state.question
        output = run_specialist(
            "clarification",
            store,
            llm,
            cfg,
            _inputs(store)
            | {
                "plan": store.read(store.state.plan_stage, store.state.plan_name),
                "question": question,
            },
            ["plan", "question"],
        )
        store.event(
            "clarification.answer",
            {"question": question, "markdown": output.markdown, "plan_hash": store.state.plan_hash},
        )
        store.update(question="", question_kind="", question_options=[], status="PENDING_REVIEW")
        publish(store)
        return store.state
    changes_requested = _changes_requested(store)
    store.update(status="RUNNING")
    while True:
        if not store.valid(2):
            store.update(stage=2)
            workspace = _inputs(store) | _reframe_inputs(store)
            required = _with_replies(
                workspace,
                HYPOTHESES_INPUT,
                *(["reframe_objections"] if "reframe_objections" in workspace else []),
            )
            output = _agent(store, llm, cfg, "framing", 2, "research_brief", workspace, required)
            options = (
                ["ASK_RESEARCHER", "STOP"]
                if output.needs_input
                else ["DESIGN", "STOP"] + (["ASK_RESEARCHER"] if output.question.strip() else [])
            )
            route = _route(store, judge, cfg, options, [output])
            store.write(2, "route.json", json.dumps({"choice": route}))
            store.complete(
                2,
                [(1, "hypothesis.md")],
                ["research_brief.md", "research_brief.json", "route.json"],
            )
        else:
            output = _load(store, 2, "research_brief")
        if store.state.profile is not None and store.state.profile.research_type is None:
            inferred = [
                e
                for e in store.events()
                if e["kind"] == "activity"
                and e["data"].get("type") == "exd.profile.inferred"
                and e["data"].get("payload", {}).get("attempt") == store.state.attempt
            ]
            if not inferred:
                infer_research_type(store, llm, cfg, output)
        if not store.valid(3):
            store.update(stage=2)
            route = json.loads(store.read(2, "route.json"))["choice"]
            if route == "ASK_RESEARCHER":
                _ask(store, [output])
                return store.state
            if route == "STOP":
                store.update(status="STOPPED")
                return store.state
            profiled = store.state.profile is not None
            if profiled and _resources_gate(store, llm, cfg, integrations):
                return store.state
            store.update(stage=3)
            workspace = _inputs(store) | {"research_brief": output.markdown}
            if profiled:
                workspace["resources"] = store.read(3, "resources.md")
            designed = _agent(
                store,
                llm,
                cfg,
                "design",
                3,
                "draft_plan",
                workspace,
                _with_replies(
                    workspace, HYPOTHESES_INPUT, "research_brief", *(["resources"] * profiled)
                ),
                review_llm=review_llm,
            )
            extras = []
            if profiled:
                extras += ["resources.json", "resources.md"]
                designed.markdown = _resource_notes(store, designed.markdown)
                store.write(3, "draft_plan.md", designed.markdown)
                _index_plan(store, llm, cfg, 3, designed.markdown)
                extras.append("index.json")
            store.complete(
                3,
                [(1, "hypothesis.md"), (2, "research_brief.md")],
                ["draft_plan.md", "draft_plan.json", *extras],
            )
            _plan(store, 3, "draft_plan.md")
            if designed.needs_input:
                _ask(store, [designed])
                return store.state
        designed = _load(store, 3, "draft_plan")
        if designed.needs_input:
            if store.state.plan_stage is None:
                _plan(store, 3, "draft_plan.md")
            _ask(store, [designed])
            return store.state
        pending = None if store.valid(4) else _pending_revision(store)
        if pending:
            _run_revision(store, llm, cfg, pending["plan"], pending["objections"], review_llm)
        redecide = False
        # Code checks (seeds, index) run on the draft too, so the first revision fixes them
        # instead of the final check finding them after the revision loop.
        early = (
            _deterministic_findings(store, 3, "draft_plan.md")
            if store.state.profile is not None and not changes_requested
            else []
        )
        if store.valid(4) and _forced_stop(store, 4):
            method, skeptic = (
                _load(store, 4, name) for name in ("methodologist_review", "skeptic_review")
            )
            redecide = _critic_options(store, cfg, method, skeptic, changes_requested, early)[
                0
            ] != ["STOP"]
        if not store.valid(4) or redecide:
            store.update(stage=4)
            draft = (
                _revision_input_plan(store) if changes_requested else store.read(3, "draft_plan.md")
            )
            if not redecide:
                inputs = _inputs(store, critic_round=True) | {
                    "research_brief": store.read(2, "research_brief.md"),
                    "plan": draft,
                }
                needed = _with_replies(inputs, HYPOTHESES_INPUT, "plan")
                method = _agent(
                    store,
                    llm,
                    cfg,
                    "methodologist",
                    4,
                    "methodologist_review",
                    inputs,
                    needed,
                    review_llm=review_llm,
                )
                skeptic = _agent(
                    store,
                    llm,
                    cfg,
                    "skeptic",
                    4,
                    "skeptic_review",
                    inputs,
                    needed,
                    review_llm=review_llm,
                )
            options, forced = _critic_options(store, cfg, method, skeptic, changes_requested, early)
            route = _route(store, judge, cfg, options, [method, skeptic])
            if route == "ASK_RESEARCHER":
                store.write(4, "route.json", json.dumps({"choice": route}))
                store.complete(
                    4,
                    [(1, "hypothesis.md"), (2, "research_brief.md"), (3, "draft_plan.md")],
                    [
                        "methodologist_review.md",
                        "methodologist_review.json",
                        "skeptic_review.md",
                        "skeptic_review.json",
                        "route.json",
                    ],
                )
                _ask(store, [method, skeptic])
                return store.state
            if route == "REFRAME":
                _reframe(
                    store,
                    {"methodologist_review": method.markdown, "skeptic_review": skeptic.markdown},
                )
                continue
            if route == "REVISE":
                objections = {
                    "methodologist_review": method.markdown,
                    "skeptic_review": skeptic.markdown,
                }
                if fixes := [f.model_dump() for f in early if _major_fix(f)]:
                    objections["deterministic_findings"] = json.dumps(fixes, ensure_ascii=False)
                _revision(store, llm, cfg, objections, review_llm)
                changes_requested = False
            else:
                store.write(
                    4,
                    "response.md",
                    "No revision requested; objections accompany the fresh final check.",
                )
                store.write(4, "revised_plan.md", draft)
                extras = []
                if store.state.profile is not None:
                    if draft == store.read(3, "draft_plan.md"):
                        store.write(4, "index.json", store.read(3, "index.json"))
                    else:
                        # A requested change left unrevised: the index must match this plan.
                        _index_plan(store, llm, cfg, 4, draft)
                    extras.append("index.json")
                store.write(4, "route.json", json.dumps({"choice": route, "forced": forced}))
                store.complete(
                    4,
                    [(1, "hypothesis.md"), (2, "research_brief.md"), (3, "draft_plan.md")],
                    [
                        "methodologist_review.md",
                        "methodologist_review.json",
                        "skeptic_review.md",
                        "skeptic_review.json",
                        "response.md",
                        "revised_plan.md",
                        "route.json",
                        *extras,
                    ],
                )
                _plan(store, 4, "revised_plan.md")
                if route == "STOP":
                    store.update(status="INCOMPLETE")
                    return store.state
        fourth_route = json.loads(store.read(4, "route.json"))["choice"]
        if fourth_route == "ASK_RESEARCHER":
            if (store.path / "stage-04" / "methodologist_review.json").is_file():
                asking = [
                    _load(store, 4, "methodologist_review"),
                    _load(store, 4, "skeptic_review"),
                ]
            else:
                asking = [_load(store, 4, "revised_plan")]
            _ask(store, asking)
            return store.state
        if store.state.plan_stage != 4 or store.state.plan_name != "revised_plan.md":
            _plan(store, 4, "revised_plan.md")
        if fourth_route == "STOP":
            store.update(status="INCOMPLETE", review_complete=False)
            return store.state
        review_names = _final_review_names(store)
        redecide = False
        if store.valid(5) and _forced_stop(store, 5):
            final = _load(store, 5, "final_review")
            redecide = _final_options(
                store,
                cfg,
                final,
                _maintained(store, review_names),
                _previous_final(store) is None,
            )[0] != ["STOP"]
        if not store.valid(5) or redecide:
            store.update(stage=5)
            if not redecide:
                inputs = _inputs(store) | {
                    "research_brief": store.read(2, "research_brief.md"),
                    "plan": store.read(store.state.plan_stage, store.state.plan_name),
                }
                deterministic = []
                if store.state.profile is not None:
                    deterministic = _deterministic_findings(store)
                    text = json.dumps([f.model_dump() for f in deterministic], ensure_ascii=False)
                    store.write(5, "deterministic_findings.json", text)
                    inputs["deterministic_findings"] = text
                for name in review_names:
                    inputs[name] = store.read(4, name + ".md")
                earlier = _previous_final(store)
                prior = previous_majors(earlier[0]) if earlier else []
                convergent = []
                if earlier:
                    inputs["previous_final_findings"] = json.dumps(
                        prior, ensure_ascii=False, indent=2
                    )
                    inputs["plan_diff_since_last_final"] = _plan_diff(earlier[1], inputs["plan"])
                    store.write(
                        5, "previous_final_findings.json", inputs["previous_final_findings"]
                    )
                    store.write(
                        5, "plan_diff_since_last_final.md", inputs["plan_diff_since_last_final"]
                    )
                    convergent = ["previous_final_findings", "plan_diff_since_last_final"]
                needed = _with_replies(
                    inputs,
                    HYPOTHESES_INPUT,
                    "research_brief",
                    "plan",
                    *(n for n in review_names if n != "response"),
                    *(["deterministic_findings"] if store.state.profile is not None else []),
                    *convergent,
                )
                final = _agent(
                    store,
                    llm,
                    cfg,
                    "final_check",
                    5,
                    "final_review",
                    inputs,
                    needed,
                    review_llm=review_llm,
                )
                if earlier:
                    converge_final(final, prior, earlier[1], inputs["plan"])
                if adjustments := severity_adjustments(final):
                    store.write(5, "final_review.md", final.markdown.rstrip() + adjustments)
                seen = {(f.id, f.issue) for f in final.findings}
                final.findings.extend(f for f in deterministic if (f.id, f.issue) not in seen)
                store.write(5, "final_review.json", _metadata(final))
            maintained = _maintained(store, review_names)
            options, forced = _final_options(
                store, cfg, final, maintained, _previous_final(store) is None
            )
            route = _route(store, judge, cfg, options, [final, *maintained])
            store.write(5, "route.json", json.dumps({"choice": route, "forced": forced}))
            store.complete(
                5,
                [
                    (1, "hypothesis.md"),
                    (2, "research_brief.md"),
                    (4, "revised_plan.md"),
                ]
                + [(4, name + ".md") for name in review_names],
                ["final_review.md", "final_review.json", "route.json"]
                + (["deterministic_findings.json"] if store.state.profile is not None else [])
                + [
                    name
                    for name in ("previous_final_findings.json", "plan_diff_since_last_final.md")
                    if (store.path / "stage-05" / name).is_file()
                ],
            )
        else:
            final = _load(store, 5, "final_review")
        if not store.valid(6):
            route = json.loads(store.read(5, "route.json"))["choice"]
            if route == "ASK_RESEARCHER":
                _ask(store, [final])
                return store.state
            if route in {"REFRAME", "REVISE"}:
                objections = {
                    "methodologist_review": store.read(4, "methodologist_review.md"),
                    "skeptic_review": store.read(4, "skeptic_review.md"),
                    "final_objections": final.markdown,
                }
                if route == "REFRAME":
                    _reframe(store, objections)
                else:
                    _revision(store, llm, cfg, objections, review_llm)
                continue
            if route == "STOP":
                store.update(status="INCOMPLETE", review_complete=False)
                return store.state
            store.update(stage=6, review_complete=True, status="PENDING_REVIEW")
            store.write(
                6,
                "researcher_review.md",
                "Scientific review: reviewed complete. Researcher acceptance: pending.\n\n"
                + store.read(store.state.plan_stage, store.state.plan_name)
                + _open_evidence(final, _maintained(store, review_names)),
            )
            replies = [e["data"] for e in store.events() if e["kind"] == "researcher.response"]
            store.write(
                6,
                "decisions.jsonl",
                "".join(json.dumps(reply, ensure_ascii=False) + "\n" for reply in replies),
            )
            store.complete(
                6, [(4, "revised_plan.md"), (5, "final_review.md")], ["researcher_review.md"]
            )
        if hold_at_review and store.state.approval != "approved":
            store.update(stage=6, status="PENDING_REVIEW")
            return store.state
        store.update(stage=7)
        if not store.valid(7):
            publish(store)
        store.update(status="PUBLISHED")
        return store.state


def respond(
    store: RunStore,
    *,
    action: str,
    text: str,
    researcher: str,
    revision: int,
    option: int | None = None,
) -> RunState:
    with store.execution(), store.coalesced():
        if revision != store.state.attempt:
            raise WorkflowError("Stale response revision")
        if action not in {"ANSWER", "APPROVE", "REQUEST_CHANGES", "CLARIFY", "REJECT"}:
            raise WorkflowError("Unknown researcher action")
        if store.state.approval == "rejected":
            raise WorkflowError("Run was rejected; no further responses are accepted")
        if option is None and not text.strip():
            raise WorkflowError("Response and researcher identity must be nonblank")
        if not researcher.strip():
            raise WorkflowError("Response and researcher identity must be nonblank")
        options = store.state.question_options
        secondary = store.state.question_kind == "REVIEW"
        # A Platform run that stopped INCOMPLETE, or whose reviewer failed, waits for the
        # researcher; CLI runs keep the guard.
        incomplete = store.state.status in {"INCOMPLETE", "REVIEW_ERROR"} and bool(
            read_run_meta(store.path).get("platform_run_id")
        )
        if incomplete and action not in {"APPROVE", "REQUEST_CHANGES"}:
            raise WorkflowError("An incomplete review takes APPROVE or REQUEST_CHANGES")
        if secondary and (
            store.state.status != "AWAITING_RESEARCHER"
            or action not in {"APPROVE", "REQUEST_CHANGES"}
        ):
            raise WorkflowError("A secondary REVIEW takes APPROVE or REQUEST_CHANGES")
        if option is not None and (
            action != "ANSWER"
            or isinstance(option, bool)
            or not options
            or not 1 <= option <= len(options)
        ):
            raise WorkflowError("Option must be a listed number for a pending ANSWER question")
        if (
            not secondary
            and action == "ANSWER"
            and (
                store.state.question_kind != "ANSWER" or store.state.status != "AWAITING_RESEARCHER"
            )
        ):
            raise WorkflowError("No pending question for ANSWER")
        if (
            not secondary
            and action != "ANSWER"
            and (store.state.plan_hash is None or not (incomplete or store.valid(6)))
        ):
            raise WorkflowError("No current presented plan")
        if store.state.plan_hash is not None:
            current = store.read(store.state.plan_stage, store.state.plan_name)
            if hashlib.sha256(current.encode()).hexdigest() != store.state.plan_hash:
                raise WorkflowError("Current plan identity is stale")
        if not secondary and action == "REQUEST_CHANGES":
            settings = store.config().get("run") or {}
            if "max_refinements" not in settings or "task_max_tokens" not in settings:
                raise WorkflowError("Pinned run limits are missing from the run configuration")
            if store.state.refinements >= settings["max_refinements"]:
                raise WorkflowError("Refinement allowance exhausted")
            if store.state.llm_token_limit - store.state.llm_tokens < _refine_reserve(
                "REQUEST_CHANGES",
                settings["task_max_tokens"],
                profiled=store.state.profile is not None,
            ):
                raise WorkflowError("Revision must preserve final-check allowance")
        if (
            not secondary
            and not incomplete
            and action == "APPROVE"
            and (not store.state.review_complete or not store.valid(5))
        ):
            raise WorkflowError("Approval requires current complete independent review")
        resumed = _answer_revision(store) if action == "ANSWER" else None
        if resumed is not None:
            resumed["objections"] = _with_finding_records(store, resumed["objections"])
        sources = json.dumps(store.sources(), ensure_ascii=False, sort_keys=True)
        brief = store.read(2, "research_brief.md") if store.valid(2) else ""
        record = {
            "action": action,
            "text": text,
            "researcher": researcher,
            "revision": revision,
            "timestamp": datetime.now(UTC).isoformat(),
            "plan_hash": store.state.plan_hash,
            "question": store.state.question,
            "option": option,
            "option_text": options[option - 1] if option is not None else None,
            "source_brief_hash": hashlib.sha256((sources + brief).encode()).hexdigest(),
        }
        if secondary:
            request = next(
                e["data"] for e in reversed(store.events()) if e["kind"] == "review.requested"
            )
            record["review_of"] = request["step_key"]
        store.event("researcher.response", record)
        path = store.path / "stage-06" / "decisions.jsonl"
        history = path.read_text(encoding="utf-8") if path.is_file() else ""
        store.write(6, "decisions.jsonl", history + json.dumps(record, ensure_ascii=False) + "\n")
        if secondary:
            store.update(status="RUNNING", question="", question_kind="", question_options=[])
        elif action == "ANSWER" and resumed:
            # A reply to a review-stage question goes straight to revision, then the
            # rejoinders and a fresh final check judge the revised plan.
            store.event("revision.pending", {"attempt": store.state.attempt, **resumed})
            store.archive_from(4)
            store.update(stage=4, status="RUNNING", question="", question_kind="")
        elif action == "ANSWER":
            store.archive_from(2)
            store.update(stage=2, status="RUNNING", question="", question_kind="")
        elif action == "REQUEST_CHANGES":
            store.archive_from(4)
            store.update(stage=4, status="REQUESTED_CHANGES", question="", question_kind="")
        elif action == "CLARIFY":
            store.update(
                status="RUNNING",
                question=text,
                question_kind="CLARIFY",
                question_options=[],
            )
        elif action == "APPROVE":
            store.update(approval="approved")
        else:
            store.update(approval="rejected", status="REJECTED")
        if (
            not secondary
            and action in {"APPROVE", "REJECT", "CLARIFY"}
            and store.state.review_complete
        ):
            from experiment_design.publication.plan import publish

            publish(store)
        if incomplete and action == "APPROVE":
            from experiment_design.publication.plan import publish

            store.update(stage=7)
            publish(store, allow_incomplete=True)
            store.update(status="PUBLISHED")
        return store.state
