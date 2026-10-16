"""Selection must include reverse dependents and fail closed when evidence is missing."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "affected_tests.py"


def selector():
    assert SCRIPT.is_file(), "affected selector must exist"
    spec = importlib.util.spec_from_file_location("affected_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(root, name, text=""):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    for name, text in {
        "src/experiment_design/__init__.py": "",
        "src/experiment_design/api/__init__.py": "from .leaf import VALUE\n",
        "src/experiment_design/api/leaf.py": "VALUE = 1\n",
        "src/experiment_design/other.py": "VALUE = 2\n",
        "tests/__init__.py": "",
        "tests/test_boundary.py": "from experiment_design.api import VALUE\n",
        "tests/test_other.py": "from experiment_design.other import VALUE\n",
    }.items():
        write(tmp_path, name, text)
    return tmp_path


def test_relative_import_and_package_initializer_reach_boundary(project):
    result = selector().select(project, ["src/experiment_design/api/leaf.py"])
    assert result["full"] is False
    assert result["tests"] == ["tests/test_boundary.py"]


def test_changed_test_includes_importing_tests(project):
    write(project, "tests/test_consumer.py", "from tests.test_boundary import VALUE\n")
    assert selector().select(project, ["tests/test_boundary.py"])["full"] is True


def test_nested_import_and_dotted_patch_target_reach_tests(project):
    write(
        project, "tests/test_nested.py", "def helper():\n from experiment_design.api import leaf\n"
    )
    write(project, "tests/test_patch.py", 'TARGET = "experiment_design.api.leaf.VALUE"\n')
    assert selector().select(project, ["src/experiment_design/api/leaf.py"])["tests"] == [
        "tests/test_boundary.py",
        "tests/test_nested.py",
        "tests/test_patch.py",
    ]


@pytest.mark.parametrize(
    "path",
    [
        "uv.lock",
        "pyproject.toml",
        ".github/workflows/tests.yml",
        "scripts/affected_tests.py",
        "tests/support/providers.py",
        "tests/fixtures/reference.json",
        "tests/conftest.py",
        "src/experiment_design/contracts.py",
        "src/experiment_design/runtime/llm.py",
        "src/experiment_design/persistence/store.py",
        "src/experiment_design/science/stats.py",
        "src/experiment_design/workflow/engine.py",
        "src/experiment_design/packs/ml/pack.yaml",
        "docs/examples/ml-experiment-plan.example.md",
        "AGENTS.md",
        "new_module.py",
        "tests/test_deleted.py",
        "../outside.py",
    ],
)
def test_shared_unknown_and_deleted_paths_run_full(project, path):
    assert selector().select(project, [path])["full"] is True


def test_known_empty_diff_and_explicit_docs_skip(project):
    for paths in ([], ["README.md"], ["docs/testing.md"]):
        result = selector().select(project, paths)
        assert result["tests"] == [] and result["full"] is False


def test_unparseable_graph_runs_full(project):
    write(project, "src/experiment_design/api/leaf.py", "invalid ! python")
    assert selector().select(project, ["tests/test_other.py"])["full"] is True


def git(root, *args):
    hooks = root / ".empty-hooks"
    hooks.mkdir(exist_ok=True)
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={hooks}", *args],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def test_staged_change_is_not_hidden_by_unstaged_restore(project):
    git(project, "init", "-q")
    git(project, "add", ".")
    git(
        project,
        "-c",
        "user.name=Selector Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    target = "tests/test_boundary.py"
    original = (project / target).read_bytes()
    write(project, target, "# staged change\n")
    git(project, "add", target)
    (project / target).write_bytes(original)
    assert selector().changed_paths(project) == [target]


def test_real_git_detects_staged_unstaged_untracked_renames_and_deletions(project):
    git(project, "init", "-q")
    git(project, "add", ".")
    git(
        project,
        "-c",
        "user.name=Selector Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    base = git(project, "rev-parse", "HEAD").decode().strip()
    git(project, "mv", "src/experiment_design/api/leaf.py", "src/experiment_design/api/renamed.py")
    (project / "tests/test_other.py").unlink()
    write(project, "tests/test_untracked.py", "def test_new(): pass\n")
    write(project, "tests/test_boundary.py", "# unstaged\n")
    paths = selector().changed_paths(project, base)
    assert set(paths) == {
        "src/experiment_design/api/leaf.py",
        "src/experiment_design/api/renamed.py",
        "tests/test_other.py",
        "tests/test_untracked.py",
        "tests/test_boundary.py",
    }
    assert selector().select(project, paths)["full"] is True


def test_missing_git_or_base_falls_back_to_full(project):
    result = subprocess.run(
        ["python", str(SCRIPT), "--base", "missing-ref"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["full"] is True


def test_explicit_empty_target_cannot_be_treated_as_clean_local_diff(project):
    git(project, "init", "-q")
    git(project, "add", ".")
    git(
        project,
        "-c",
        "user.name=Selector Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    result = subprocess.run(
        ["python", str(SCRIPT), "--base", ""],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["full"] is True


def test_ignored_importable_files_are_changed_but_caches_are_excluded(project):
    git(project, "init", "-q")
    write(
        project,
        ".gitignore",
        "src/ignored.py\ntests/ignored.json\nscripts/ignored.py\ntests/__pycache__/\nsrc/.env\n",
    )
    git(project, "add", ".")
    git(
        project,
        "-c",
        "user.name=Selector Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    for name in (
        "src/ignored.py",
        "tests/ignored.json",
        "scripts/ignored.py",
        "tests/__pycache__/ignored.py",
        "src/.env",
    ):
        write(project, name)
    assert selector().changed_paths(project) == [
        "scripts/ignored.py",
        "src/ignored.py",
        "tests/ignored.json",
    ]


def test_ignored_example_asset_is_changed_and_runs_full(project):
    git(project, "init", "-q")
    write(project, ".gitignore", "docs/*\n")
    git(project, "add", ".")
    git(
        project,
        "-c",
        "user.name=Selector Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    target = "docs/examples/grade7-reintroduced.md"
    write(project, target, "# Removed example reintroduced\n")
    paths = selector().changed_paths(project)
    assert paths == [target]
    assert selector().select(project, paths)["full"] is True


@pytest.mark.parametrize(
    "plan", [[], {}, {"full": False, "tests": [3]}, {"full": False, "tests": ["../outside.py"]}]
)
def test_invalid_stored_plan_is_rejected(project, plan):
    with pytest.raises(ValueError):
        selector().validate_plan(project, plan)


@pytest.mark.parametrize(
    "state,expected",
    [
        ("success", True),
        ("failure", False),
        ("cancelled", False),
        ("skipped", False),
    ],
)
def test_gate_requires_successful_selection(state, expected):
    needs = {
        "selection": {"result": state, "outputs": {"tests": "false"}},
        "lint": {"result": "success"},
        "tests": {"result": "skipped"},
    }
    assert selector().gate_ok(needs) is expected


@pytest.mark.parametrize("state", ["failure", "cancelled", "skipped", "unknown"])
def test_gate_rejects_failed_or_missing_required_test_lane(state):
    needs = {
        "selection": {"result": "success", "outputs": {"tests": "true"}},
        "lint": {"result": "success"},
        "tests": {"result": state},
    }
    assert selector().gate_ok(needs) is False


def test_gate_rejects_missing_jobs():
    assert selector().gate_ok({}) is False
