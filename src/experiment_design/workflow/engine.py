"""Seven direct stages over immutable inputs and reviewed Markdown."""

import difflib
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from experiment_design.agents.prompts import snapshot
from experiment_design.agents.tools import run_specialist
from experiment_design.config import ExdConfig, RunConfig
from experiment_design.contracts import AgentOutput, RunInterrupted, RunState, WorkflowError
from experiment_design.persistence.store import RunStore
from experiment_design.runtime.jev import Judge, Question
from experiment_design.runtime.llm import LLMClient
from experiment_design.vocabulary import REVIEW_ROLES

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
    output = run_specialist(role, store, client, cfg, workspace, required)
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
    store.update(
        status="AWAITING_RESEARCHER",
        question="\n".join(questions),
        question_kind="ANSWER",
        question_options=asking[0].options if len(asking) == 1 else [],
    )


# Specialist runs a path still needs, so the fresh final check always keeps its allowance.
_REFINE_TASKS = {
    "REVISE": 4,  # revision, two rejoinders, final check
    "REFRAME": 5,  # framing, design, two critics, final check
    "REQUEST_CHANGES": 6,  # two critics, revision, two rejoinders, final check
}


def _refine_reserve(path: str, task_max_tokens: int) -> int:
    return _REFINE_TASKS[path] * task_max_tokens


def _can_refine(store: RunStore, cfg: RunConfig, path: str) -> bool:
    return (
        store.state.refinements < cfg.max_refinements
        and store.state.llm_token_limit - store.state.llm_tokens
        >= _refine_reserve(path, cfg.task_max_tokens)
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


def _critic_options(
    store: RunStore,
    cfg: RunConfig,
    method: AgentOutput,
    skeptic: AgentOutput,
    changes_requested: bool,
) -> tuple[list[str], bool]:
    """Legal routes after the critics, and whether STOP stands alone only because of limits."""
    material = any(f.severity == "MAJOR" for o in [method, skeptic] for f in o.findings)
    options = (
        []
        if material or changes_requested or method.needs_input or skeptic.needs_input
        else ["CHECK"]
    )
    paths = ["REVISE"] if changes_requested else ["REVISE", "REFRAME"]
    available = [path for path in paths if _can_refine(store, cfg, path)]
    options += available
    if method.question.strip() or skeptic.question.strip():
        options.append("ASK_RESEARCHER")
    options.append("STOP")
    return options, options == ["STOP"] and available != paths


def _final_review_names(store: RunStore) -> list[str]:
    optional = ("final_objections", "plan_diff", "methodologist_rejoinder", "skeptic_rejoinder")
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
    store: RunStore, cfg: RunConfig, final: AgentOutput, maintained: list[AgentOutput]
) -> tuple[list[str], bool]:
    material = (
        final.needs_input
        or any(f.severity == "MAJOR" for f in final.findings)
        or any(f.severity == "MAJOR" for o in maintained for f in o.findings)
    )
    options = [] if material else ["PRESENT"]
    paths = ["REVISE", "REFRAME"]
    available = [path for path in paths if _can_refine(store, cfg, path)]
    options += available
    if final.question.strip():
        options.append("ASK_RESEARCHER")
    options.append("STOP")
    return options, options == ["STOP"] and available != paths


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
    store.event(
        "revision.pending",
        {"attempt": store.state.attempt, "plan": old_plan, "objections": objections},
    )
    store.update(refinements=store.state.refinements + 1)
    store.archive_from(4)
    _run_revision(store, llm, cfg, old_plan, objections, review_llm)


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
        for name, text in objections.items():
            store.write(4, name + ".md", text)
        store.write(4, "response.md", _response(output))
        store.write(4, "plan_diff.md", _plan_diff(old_plan, output.markdown))
        asking = output.needs_input and bool(output.question.strip())
        store.write(
            4, "route.json", json.dumps({"choice": "ASK_RESEARCHER" if asking else "CHECK"})
        )
    outputs = [
        "revised_plan.md",
        "revised_plan.json",
        "response.md",
        "plan_diff.md",
        "route.json",
    ] + [name + ".md" for name in objections]
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
        if not store.valid(3):
            store.update(stage=2)
            route = json.loads(store.read(2, "route.json"))["choice"]
            if route == "ASK_RESEARCHER":
                _ask(store, [output])
                return store.state
            if route == "STOP":
                store.update(status="STOPPED")
                return store.state
            store.update(stage=3)
            workspace = _inputs(store) | {"research_brief": output.markdown}
            designed = _agent(
                store,
                llm,
                cfg,
                "design",
                3,
                "draft_plan",
                workspace,
                _with_replies(workspace, HYPOTHESES_INPUT, "research_brief"),
                review_llm=review_llm,
            )
            store.complete(
                3,
                [(1, "hypothesis.md"), (2, "research_brief.md")],
                ["draft_plan.md", "draft_plan.json"],
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
        if store.valid(4) and _forced_stop(store, 4):
            method, skeptic = (
                _load(store, 4, name) for name in ("methodologist_review", "skeptic_review")
            )
            redecide = _critic_options(store, cfg, method, skeptic, changes_requested)[0] != [
                "STOP"
            ]
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
            options, forced = _critic_options(store, cfg, method, skeptic, changes_requested)
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
                _revision(
                    store,
                    llm,
                    cfg,
                    {"methodologist_review": method.markdown, "skeptic_review": skeptic.markdown},
                    review_llm,
                )
                changes_requested = False
            else:
                store.write(
                    4,
                    "response.md",
                    "No revision requested; objections accompany the fresh final check.",
                )
                store.write(4, "revised_plan.md", draft)
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
            redecide = _final_options(store, cfg, final, _maintained(store, review_names))[0] != [
                "STOP"
            ]
        if not store.valid(5) or redecide:
            store.update(stage=5)
            if not redecide:
                inputs = _inputs(store) | {
                    "research_brief": store.read(2, "research_brief.md"),
                    "plan": store.read(store.state.plan_stage, store.state.plan_name),
                }
                for name in review_names:
                    inputs[name] = store.read(4, name + ".md")
                needed = _with_replies(
                    inputs,
                    HYPOTHESES_INPUT,
                    "research_brief",
                    "plan",
                    *(n for n in review_names if n != "response"),
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
            maintained = _maintained(store, review_names)
            options, forced = _final_options(store, cfg, final, maintained)
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
                ["final_review.md", "final_review.json", "route.json"],
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
                + store.read(store.state.plan_stage, store.state.plan_name),
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
        if option is not None and (
            action != "ANSWER"
            or isinstance(option, bool)
            or not options
            or not 1 <= option <= len(options)
        ):
            raise WorkflowError("Option must be a listed number for a pending ANSWER question")
        if action == "ANSWER" and (
            store.state.question_kind != "ANSWER" or store.state.status != "AWAITING_RESEARCHER"
        ):
            raise WorkflowError("No pending question for ANSWER")
        if action != "ANSWER" and (store.state.plan_hash is None or not store.valid(6)):
            raise WorkflowError("No current presented plan")
        if store.state.plan_hash is not None:
            current = store.read(store.state.plan_stage, store.state.plan_name)
            if hashlib.sha256(current.encode()).hexdigest() != store.state.plan_hash:
                raise WorkflowError("Current plan identity is stale")
        if action == "REQUEST_CHANGES":
            settings = store.config().get("run") or {}
            if "max_refinements" not in settings or "task_max_tokens" not in settings:
                raise WorkflowError("Pinned run limits are missing from the run configuration")
            if store.state.refinements >= settings["max_refinements"]:
                raise WorkflowError("Refinement allowance exhausted")
            if store.state.llm_token_limit - store.state.llm_tokens < _refine_reserve(
                "REQUEST_CHANGES", settings["task_max_tokens"]
            ):
                raise WorkflowError("Revision must preserve final-check allowance")
        if action == "APPROVE" and (not store.state.review_complete or not store.valid(5)):
            raise WorkflowError("Approval requires current complete independent review")
        resumed = _answer_revision(store) if action == "ANSWER" else None
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
        store.event("researcher.response", record)
        path = store.path / "stage-06" / "decisions.jsonl"
        history = path.read_text(encoding="utf-8") if path.is_file() else ""
        store.write(6, "decisions.jsonl", history + json.dumps(record, ensure_ascii=False) + "\n")
        if action == "ANSWER" and resumed:
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
        if action in {"APPROVE", "REJECT", "CLARIFY"} and store.state.review_complete:
            from experiment_design.publication.plan import publish

            publish(store)
        return store.state
