"""Conservative deterministic test selection shared by edit checks, pre-push and CI."""

import argparse
import ast
import fnmatch
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

POLICY = Path(__file__).with_name("test-lanes.json")
EXCLUDED = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    ".worktrees",
    ".kilo",
    "runs",
    "research",
    "pilot",
}


def relevant(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return not (set(parts) & EXCLUDED) and not any(
        part.startswith(".env") or part == "exd.yaml" for part in parts
    )


def full(reason: str) -> dict:
    return {"full": True, "tests": ["tests"], "lanes": ["all"], "reason": reason}


def matches(path: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def import_graph(root: Path) -> tuple[dict[str, set[str]], set[str]]:
    modules = {}
    for directory in ("src", "tests"):
        for path in (root / directory).rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            if not relevant(relative):
                continue
            parts = list(PurePosixPath(relative).with_suffix("").parts)
            if directory == "src":
                parts.pop(0)
            if parts[-1] == "__init__":
                parts.pop()
            modules[".".join(parts)] = relative
    graph = {path: set() for path in modules.values()}
    for module, path in modules.items():
        tree = ast.parse((root / path).read_text(encoding="utf-8"), filename=path)
        package = module if path.endswith("/__init__.py") else module.rpartition(".")[0]
        dependencies = {package} if package else set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                dependencies.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                name = node.module or ""
                if node.level:
                    name = importlib.util.resolve_name("." * node.level + name, package)
                dependencies.add(name)
                dependencies.update(f"{name}.{alias.name}" for alias in node.names)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith(("experiment_design.", "tests."))
            ):
                # String targets used by monkeypatch/importorskip are also dependencies.
                dependencies.add(node.value)
        for dependency in dependencies:
            pieces = dependency.split(".")
            for length in range(1, len(pieces) + 1):
                target = modules.get(".".join(pieces[:length]))
                if target and target != path:
                    graph[path].add(target)
        if path.startswith("tests/"):
            for parent in PurePosixPath(path).parents:
                fixture = (parent / "conftest.py").as_posix()
                if fixture in graph:
                    graph[path].add(fixture)
    tests = {
        path
        for path in graph
        if path.startswith("tests/") and PurePosixPath(path).name.startswith("test_")
    }
    return graph, tests


def select(root: Path, paths: list[str]) -> dict:
    try:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        graph, tests = import_graph(root)
        if not tests:
            return full("test inventory unavailable")
        selected = set()
        for raw in paths:
            path = raw.replace("\\", "/")
            parts = PurePosixPath(path)
            if parts.is_absolute() or ".." in parts.parts or ":" in path:
                return full(f"unmapped path: {path}")
            if matches(path, policy["shared"]):
                return full(f"shared dependency: {path}")
            if matches(path, policy["ignore"]):
                continue
            if path not in graph:
                return full(f"unmapped or deleted path: {path}")
            if path.startswith("tests/") and (
                path not in tests or any(path in deps for deps in graph.values())
            ):
                return full(f"shared test helper: {path}")
            affected = {path}
            while True:
                dependents = {p for p, deps in graph.items() if deps & affected}
                if dependents <= affected:
                    break
                affected.update(dependents)
            owners = tests & affected
            if not owners:
                return full(f"no test owner: {path}")
            selected.update(owners)
        lanes = sorted(
            name
            for name, pattern in policy["lanes"].items()
            if any(matches(path, [pattern]) for path in selected)
        )
        return {
            "full": False,
            "tests": sorted(selected),
            "lanes": lanes,
            "reason": "reverse imports" if selected else "known empty diff or explicit docs",
        }
    except (OSError, ValueError, SyntaxError, ImportError, KeyError, TypeError) as exc:
        return full(f"selection unavailable: {type(exc).__name__}")


def changed_paths(root: Path, base: str | None = None) -> list[str]:
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout

    # A target branch always means its merge base, including all PR pushes.
    target = git("merge-base", base, "HEAD").decode().strip() if base is not None else "HEAD"
    diff = ("diff", "--name-only", "--no-renames", "-z")
    # Separate layers prevent an unstaged restore from hiding a staged change.
    tracked = git(*diff, target, "HEAD", "--")
    tracked += git(*diff, "--cached", "HEAD", "--")
    tracked += git(*diff, "--")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z")
    ignored = git(
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "-z",
        "--",
        "src",
        "tests",
        "scripts",
        "docs/examples",
    )
    paths = {part.decode("utf-8") for part in (tracked + untracked).split(b"\0") if part}
    paths.update(
        part.decode("utf-8")
        for part in ignored.split(b"\0")
        if part and relevant(part.decode("utf-8"))
    )
    return sorted(paths)


def gate_ok(needs: dict) -> bool:
    try:
        selection, lint, tests = (needs[name] for name in ("selection", "lint", "tests"))
        expected = selection["outputs"]["tests"]
        return (
            selection["result"] == lint["result"] == "success"
            and expected in ("true", "false")
            and tests["result"] == ("success" if expected == "true" else "skipped")
        )
    except (KeyError, TypeError):
        return False


def validate_plan(root: Path, plan: dict) -> dict:
    if not isinstance(plan, dict) or not isinstance(plan.get("full"), bool):
        raise ValueError("invalid test plan")
    if not isinstance(plan.get("tests"), list) or not all(
        isinstance(target, str) for target in plan["tests"]
    ):
        raise ValueError("invalid test plan")
    if plan["full"]:
        return full(plan.get("reason", "stored full plan"))
    for target in plan["tests"]:
        path = root / target.split("::", 1)[0]
        if not path.resolve().is_relative_to((root / "tests").resolve()) or not path.exists():
            raise ValueError("test target must exist under tests")
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--base", help="target branch/ref; compare against its merge base")
    modes.add_argument("--full", action="store_true")
    modes.add_argument("--changed", nargs="+", help="explicit changed paths for mapping checks")
    modes.add_argument("--files", nargs="+", help="explicit pytest targets for the edit loop")
    modes.add_argument("--lane", help="explicit owner lane for the edit loop")
    modes.add_argument("--plan-env", help="environment variable containing the immutable CI plan")
    parser.add_argument("--run", action="store_true", help="execute; default only prints JSON")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--gate-env", help="environment variable containing toJSON(needs)")
    args = parser.parse_args()
    root = Path.cwd()
    if args.gate_env:
        try:
            return 0 if gate_ok(json.loads(os.environ[args.gate_env])) else 1
        except (KeyError, ValueError, TypeError):
            return 1
    try:
        if args.full:
            plan = full("explicit full suite")
        elif args.plan_env:
            plan = validate_plan(root, json.loads(os.environ[args.plan_env]))
        elif args.files or args.lane:
            targets = args.files
            if args.lane:
                policy = json.loads(POLICY.read_text(encoding="utf-8"))
                pattern = policy["lanes"][args.lane]
                targets = sorted(
                    p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file()
                )
                if not targets:
                    raise ValueError("owner lane has no tests")
            plan = validate_plan(
                root,
                {
                    "full": False,
                    "tests": targets,
                    "lanes": [args.lane or "explicit"],
                    "reason": "explicit focused edit check",
                },
            )
        else:
            plan = select(
                root, args.changed if args.changed is not None else changed_paths(root, args.base)
            )
    except (OSError, ValueError, subprocess.SubprocessError, KeyError, TypeError) as exc:
        plan = full(f"selection unavailable: {type(exc).__name__}")
    encoded = json.dumps(plan)
    print(encoded, flush=True)
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"tests={str(bool(plan['tests'])).lower()}\nplan={encoded}\n")
    if args.run and plan["tests"]:
        command = [sys.executable, "-m", "pytest", "-q", *plan["tests"]]
        if args.fail_fast:
            command.append("-x")
        return subprocess.run(command, cwd=root, check=False).returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
