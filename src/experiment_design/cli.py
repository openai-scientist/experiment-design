"""Researcher commands for the seven-stage Markdown workflow."""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from experiment_design.config import ConfigError, ExdConfig, load_config
from experiment_design.contracts import RunState, WorkflowError
from experiment_design.persistence.store import RunStore
from experiment_design.publication import (
    clarifications,
    current_plan,
    export,
    question_text,
    render_review,
)
from experiment_design.runtime.jev import JudgeError, TypeSafeJudge
from experiment_design.runtime.llm import LLMClient, LLMError


def respond(
    store: RunStore,
    *,
    action: str,
    text: str,
    researcher: str,
    revision: int,
    option: int | None = None,
) -> RunState:
    from experiment_design.workflow.engine import respond as record_response

    return record_response(
        store, action=action, text=text, researcher=researcher, revision=revision, option=option
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="exd", description="Reviewed scientific experiment plans")
    parser.add_argument("--config", type=Path, default=Path("exd.yaml"))
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Start a new run; calls configured providers")
    run.add_argument("inputs", type=Path, nargs="+")
    for name in ("resume", "status", "review", "decide", "export"):
        command = commands.add_parser(name)
        command.add_argument("run", help="Run directory, or ID under configured runs_root")
        if name == "resume":
            command.add_argument("--llm-token-limit", type=int)
            command.add_argument("--reason", default="")
        elif name == "decide":
            command.add_argument(
                "--action",
                required=True,
                choices=["ANSWER", "APPROVE", "REQUEST_CHANGES", "CLARIFY", "REJECT"],
            )
            command.add_argument("--text", default="")
            command.add_argument(
                "--option", type=int, help="1-based option of the pending question (ANSWER only)"
            )
            command.add_argument("--by", required=True)
            command.add_argument(
                "--revision", type=int, required=True, help="Current attempt shown by status"
            )
        elif name == "export":
            command.add_argument("--out", type=Path, required=True)
            command.add_argument("--allow-draft", action="store_true")
    return parser


def _store(value: str, config_path: Path) -> RunStore:
    path = Path(value)
    if path.exists() or path.is_absolute() or len(path.parts) > 1:
        return RunStore(path)
    root = (
        load_config(config_path, require_provider_keys=False).runs_root
        if config_path.exists()
        else Path("runs")
    )
    return RunStore(root / path)


def raise_budget(store: RunStore, ceiling: int, reason: str) -> None:
    with store.execution():
        state = store.state
        if state.usage_unknown:
            raise WorkflowError("Unknown provider usage cannot be cleared by a token increase.")
        if ceiling <= max(state.llm_token_limit, state.llm_tokens):
            raise WorkflowError(
                "Token ceiling must exceed its previous limit and cumulative usage."
            )
        if not reason.strip():
            raise WorkflowError("A nonblank --reason is required for a token ceiling increase.")
        previous = state.llm_token_limit
        store.update(llm_token_limit=ceiling)
        store.event(
            "budget.increased",
            {
                "previous": previous,
                "ceiling": ceiling,
                "reason": reason,
                "llm_tokens": state.llm_tokens,
            },
        )


def _advance(store: RunStore, cfg: ExdConfig) -> None:
    from experiment_design.workflow.engine import advance

    judge = TypeSafeJudge(cfg.jev)
    llm = LLMClient(cfg.llm, os.environ[cfg.llm.api_key_env])
    try:
        advance(store, llm, judge, cfg.run)
    finally:
        llm.close()


def _status(store: RunStore) -> None:
    state = store.state
    display = state.model_dump(mode="json")
    complete = bool(
        state.review_complete
        and state.plan_stage is not None
        and store.valid(state.plan_stage)
        and store.valid(5)
        and store.valid(6)
    )
    if complete:
        try:
            current_plan(store)
        except (WorkflowError, OSError):
            complete = False
    display["review_complete"] = complete
    if not complete:
        display["approval"] = "pending"
    print(json.dumps(display, ensure_ascii=False, indent=2))
    print(f"Run directory: {store.path}")
    if state.question:
        print(f"Pending question: {question_text(state)}")
    print(f"Clarification explanations for current plan: {len(clarifications(store))}")
    if state.plan_stage is not None and state.plan_name:
        print(f"Plan: {store.path / f'stage-{state.plan_stage:02d}' / state.plan_name}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            from experiment_design.workflow.engine import start_run

            load_dotenv()
            cfg = load_config(args.config)
            store = start_run(args.inputs, cfg)
            print(f"Run directory: {store.path}", flush=True)
            _advance(store, cfg)
            _status(store)
            return 0
        store = _store(args.run, args.config)
        if args.command == "status":
            _status(store)
        elif args.command == "review":
            if store.state.plan_stage is None:
                _status(store)
            else:
                print(render_review(store))
        elif args.command == "decide":
            respond(
                store,
                action=args.action,
                text=args.text,
                researcher=args.by,
                revision=args.revision,
                option=args.option,
            )
            _status(store)
        elif args.command == "export":
            print(export(store, args.out, allow_draft=args.allow_draft))
        elif args.command == "resume":
            if args.llm_token_limit is None and args.reason:
                raise WorkflowError("--reason requires --llm-token-limit.")
            load_dotenv()
            cfg = load_config(args.config)
            if args.llm_token_limit is not None:
                raise_budget(store, args.llm_token_limit, args.reason)
            from experiment_design.workflow.engine import pin_limits

            pin_limits(store, cfg.run)
            _advance(store, cfg)
            _status(store)
        return 0
    except (ConfigError, WorkflowError, LLMError, JudgeError, OSError, ValueError) as exc:
        print(f"exd: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
