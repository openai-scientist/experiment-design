"""Researcher commands for a single canonical design workflow."""

import argparse
import os
import shlex
import sys
from collections import Counter
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from experiment_design.config import ConfigError, ExdConfig, RunConfig, load_config
from experiment_design.contracts import DecisionResponse
from experiment_design.decisions import respond
from experiment_design.output import export, render_decision, render_markdown
from experiment_design.package import PackageFormatError, all_sources, snapshot_sources
from experiment_design.runtime.jev import JudgeError, TypeSafeJudge
from experiment_design.runtime.llm import LLMClient, LLMError
from experiment_design.runtime.store import Store
from experiment_design.workflow import (
    Deps,
    WorkflowError,
    advance,
    answer_request,
    approve,
    request_changes,
    start_run,
)


def build_deps(cfg: ExdConfig) -> Deps:
    judge = TypeSafeJudge(cfg.jev)
    llm = LLMClient(cfg.llm, os.environ[cfg.llm.api_key_env])
    try:
        return Deps(Store(cfg.store), llm, judge, cfg.run)
    except Exception:
        llm.close()
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exd", description="Reviewable scientific experiment design"
    )
    parser.add_argument("--config", type=Path, default=Path("exd.yaml"))
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("hypothesis", type=Path, nargs="+")
    decide = commands.add_parser("decide")
    decide.add_argument("run")
    decide.add_argument("decision")
    decide.add_argument("--revision", type=int, required=True)
    decide.add_argument(
        "--action",
        choices=[
            "SELECT_OPTION",
            "PROPOSE_ALTERNATIVE",
            "DEFER",
            "REQUEST_CHANGES",
            "REJECT",
            "HOLD",
        ],
        required=True,
    )
    decide.add_argument("--option")
    decide.add_argument("--text", default="")
    decide.add_argument("--by", required=True)
    for command in ("resume", "status", "review", "answer", "request-changes", "approve", "export"):
        sub = commands.add_parser(command)
        sub.add_argument("run")
        if command == "answer":
            sub.add_argument("request")
            sub.add_argument("text")
        elif command == "request-changes":
            sub.add_argument("text")
            sub.add_argument("--path")
            sub.add_argument("--by", required=True)
            sub.add_argument("--revision", type=int, required=True)
        elif command == "approve":
            sub.add_argument("--version", type=int, required=True)
            sub.add_argument("--by", required=True)
        elif command == "export":
            sub.add_argument("--out", type=Path, required=True)
            sub.add_argument("--version", type=int)
        elif command == "resume":
            sub.add_argument("--llm-token-limit", type=int)
            sub.add_argument("--reason", default="Explicit operator budget increase")
    return parser


def _prefix(config: Path) -> str:
    if config.resolve() == Path("exd.yaml").resolve():
        return "exd"
    return f"exd --config {shlex.quote(str(config))}"


def _status(store: Store, rid: str, config: Path) -> None:
    record, version, usage = store.run(rid), store.version(rid), store.usage(rid)
    print(f"status: {record.status}")
    print(f"stage: {record.stage_id or 'legacy'}; schema: {record.schema_version}")
    print(f"latest version: {version.version if version else 0}")
    print(f"reviewed version: {record.reviewed_version}")
    counts = Counter(issue.status for issue in store.issues(rid))
    print(
        "issues: "
        + (", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none")
    )
    print("usage: " + ", ".join(f"{key}={value}" for key, value in usage.model_dump().items()))
    if record.status == "BUDGET_STOP":
        stops = [event.data for event in store.events(rid) if event.type == "budget.stop"]
        if stops:
            stop = stops[-1]
            detail = f"scope={stop['scope']}, reason={stop['reason']}"
            if stop.get("agent_id"):
                detail += f", agent={stop['agent_id']}"
            if "tokens_used" in stop:
                detail += f", tokens={stop['tokens_used']}/{stop['tokens_limit']}"
            if "actions_used" in stop:
                detail += f", actions={stop['actions_used']}/{stop['actions_limit']}"
            if "llm_calls" in stop:
                detail += f", calls={stop['llm_calls']}"
                if "max_turns" in stop:
                    detail += f"/{stop['max_turns']}"
            print("budget stop: " + detail)
        else:
            print("budget stop: reason unavailable in this older journal")
    for request in store.requests(rid):
        if request.status == "open":
            print(request.question)
            print(f'  {_prefix(config)} answer {rid} {request.id} "<your answer>"')
    if record.schema_version == 2:
        for decision in store.decisions(rid):
            if decision.status == "AWAITING_RESEARCHER":
                print(render_decision(decision), end="")
                if decision.purpose == "scientific":
                    print(
                        f"  {_prefix(config)} decide {rid} {decision.id} "
                        f"--revision {decision.revision} "
                        '--action PROPOSE_ALTERNATIVE --text "<your answer>" --by "<your name>"'
                    )
                    for option in decision.options:
                        option_arg = shlex.quote(option.id)
                        if os.name == "nt":
                            option_arg = "'" + option.id.replace("'", "''") + "'"
                        print(
                            f"  {_prefix(config)} decide {rid} {decision.id} "
                            f"--revision {decision.revision} "
                            f"--action SELECT_OPTION --option={option_arg} "
                            '--by "<your name>"'
                        )
                else:
                    print(
                        f"  {_prefix(config)} approve {rid} --version {version.version} "
                        '--by "<your name>"'
                    )


def _read_command(store: Store, args) -> None:
    rid = args.run
    prefix = _prefix(args.config)
    if args.command == "status":
        _status(store, rid, args.config)
    elif args.command == "review":
        run, version = store.run(rid), store.version(rid)
        if version is None:
            raise WorkflowError("No design version exists yet")
        if run.schema_version == 2:
            print(render_markdown(store.publication_snapshot(rid)), end="")
            return
        approvals = [
            a
            for a in store.approvals(rid)
            if a.version == version.version and a.design_hash == version.design_hash
        ]
        print(
            render_markdown(
                run.package,
                all_sources(run.package, store.requests(rid)),
                version,
                run.status,
                version.version,
                store.issues(rid),
                store.requests(rid),
                approval=approvals[-1] if approvals else None,
            ),
            end="",
        )
    elif args.command == "answer":
        answer_request(store, rid, args.request, args.text)
        print(f"answered {args.request}")
        if not any(r.status == "open" for r in store.requests(rid)):
            print(f"next: {prefix} resume {rid}")
    elif args.command == "decide":
        respond(
            store,
            rid,
            args.decision,
            DecisionResponse(
                revision=args.revision,
                action=args.action,
                option_id=args.option,
                text=args.text,
                researcher=args.by,
                response_id="response:" + uuid4().hex,
            ),
        )
        print(f"recorded decision {args.decision}")
        print(f"next: {prefix} resume {rid}")
    elif args.command == "request-changes":
        request_changes(
            store, rid, args.text, args.path, researcher=args.by, revision=args.revision
        )
        print(f"next: {prefix} resume {rid}")
    elif args.command == "approve":
        approve(store, rid, args.version, args.by)
        print(f"approved v{args.version}")
    elif args.command == "export":
        for path in export(store, rid, args.out, args.version).values():
            print(path)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    deps, store = None, None
    try:
        load_dotenv(dotenv_path=Path(".env"), override=False)
        cfg = load_config(args.config, require_provider_keys=args.command in {"run", "resume"})
        if args.command in {"run", "resume"}:
            package = snapshot_sources(args.hypothesis) if args.command == "run" else None
            deps = build_deps(cfg)
            store = deps.store
            rid = start_run(package, deps, cfg.model_dump(mode="json")) if package else args.run
            if args.command == "resume" and store.run(rid).config.get("run"):
                deps.run = cfg.run = RunConfig.model_validate(store.run(rid).config["run"])
            if args.command == "resume" and args.llm_token_limit is not None:
                record = store.run(rid)
                if record.schema_version != 2:
                    raise WorkflowError("schema-1 run cannot resume")
                old = record.config.get("run", {}).get("llm_tokens", cfg.run.llm_tokens)
                if args.llm_token_limit <= old:
                    raise WorkflowError("new token ceiling must exceed current ceiling")
                store.append_event(
                    rid,
                    "budget.changed",
                    {"old": old, "new": args.llm_token_limit, "reason": args.reason},
                )
                cfg.run.llm_tokens = args.llm_token_limit
                config = record.config | {"run": cfg.run.model_dump()}
                store.update_run(rid, config=config)
                deps.run = cfg.run
            try:
                status = advance(rid, deps)
            except (LLMError, JudgeError):
                print(f"run {rid}: {store.run(rid).status}")
                print(f"next: {_prefix(args.config)} resume {rid}")
                raise
            print(f"run {rid}: {status}")
            _status(store, rid, args.config)
        else:
            store = Store(cfg.store)
            _read_command(store, args)
        return 0
    except (
        WorkflowError,
        ConfigError,
        PackageFormatError,
        FileNotFoundError,
        KeyError,
        LLMError,
        JudgeError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if store:
            store.close()
        if deps:
            close = getattr(deps.llm, "close", None)
            if close:
                close()


if __name__ == "__main__":
    raise SystemExit(main())
