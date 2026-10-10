# Local tests

Run `uv run pytest -q` for the complete local suite. It replaces external
providers with scripted services while exercising real scientific calculations,
SQLite storage, workflow, CLI commands and exports. The default command excludes
the `live` marker. The migration suite remains below the 60-second threshold for additional lanes.

During an edit, run the owner file, for example:

```powershell
uv run pytest tests/test_output.py -q
```

For a specific regression, select its node:

```powershell
uv run pytest tests/test_output.py::test_export_old_version_shows_concerns_as_of_that_version -q
```

Owner files give quick feedback; run the complete local suite after changes to
shared contracts, calculations, package intake, storage, tools or workflow.
These modules also affect commands and exported scientific results.

| Production owner | Focused tests |
|---|---|
| `science/power.py` | `tests/test_power.py` |
| `science/validators.py` | `tests/test_validators.py` |
| `science/evidence.py`, `package.py` | `tests/test_package.py`, `tests/test_pilot_package.py` |
| `patch.py` | `tests/test_patch.py` |
| `config.py` | `tests/test_config.py` |
| `runtime/llm.py` | `tests/test_llm.py` |
| `runtime/jev.py` | `tests/test_jev.py` |
| `runtime/loop.py` | `tests/test_loop.py` |
| `runtime/store.py`, `stages.py` | `tests/test_store.py`, `tests/test_stages.py` |
| `agents/` | `tests/test_agents.py`, `tests/test_understanding.py` |
| `decisions.py` | `tests/test_decisions.py` |
| `review.py` | `tests/test_review.py` |
| `qualification.py` | `tests/test_qualification.py` |
| `workflow.py` | `tests/test_workflow.py`, `tests/test_stage_pipeline.py` |
| `output.py` | `tests/test_output.py`, `tests/test_publication.py` |
| `cli.py` | `tests/test_cli.py` |
| Shared contracts, test support, configuration, or an unknown owner | Complete local suite |

Before pushing, run `uv run pytest -q`. Before committing, also run:

```powershell
uv run ruff check .
uv run ruff format --check .
```

Measure slow tests with `uv run pytest -q --durations=15`. Add affected-test lanes
only when the complete local suite exceeds 60 seconds, following `AGENTS.md`.
Until then, the complete suite is the affected-change check; there is no separate
selection script or CI lane map to maintain. This repository currently has no CI
workflow definitions.

Real-provider checks use `uv run pytest -m live -q` and require provider keys.
The audit did not call real providers; no tests currently carry the `live` marker.

Keep storage and command tests when they protect different boundaries. A schema
round trip with its own generated JSON does not establish a persisted-format
contract. For negative filters, include data that would leak if the filter were
removed, and prove the test fails under that mutation.
