# Deterministic test feedback audit — 2026-10-15

The baseline passed **776 cases, 1 existing skip in 656.11s**. No tests were pruned,
weakened or moved off PR checks. The useful improvement is avoiding repeated full
runs during edits: the explicit observability check passed 2 cases in 0.06s;
the API unit owner passed 49 in 10.67s. These are measured pytest times on Windows,
with other agents sharing the machine, not controlled speed benchmarks or hosted CI times.

## Evidence and limits

Baseline command (PowerShell, repository root):

```powershell
uv run pytest -q --durations=30 --junitxml=$env:TEMP/exd-audit-baseline.xml
```

Inventory: 51 test files, 390 declarations, 6,984 Python test LOC and 779 Python
support LOC in 22 files (including fixtures, conftest and package initializers).
Expanded cases differ from declaration count because of parameterization.
After tooling: 52 test files, 406 declarations and 7,273 test LOC; original test/support
LOC are unchanged. The added selector guard suite is 289 LOC/16 declarations/42 cases;
the shared selector script is 271 LOC, counted separately from Python test support.
The [ledger](test-audit-ledger.md) records every original declaration as R and each
file's baseline result/time. This is a complete inventory and conservative retention
ledger, **not a claim that every declaration received a full semantic audit**.
Reviewed pruning candidates appear below; uncertain cases stay R.

Baseline started at HEAD `7d0e123c1f720187f2306eff7872777a9b7c319f` with existing
user edits, and finished at `83376e5ff0dbcca41f11322428a5911f2fcda58c` after external
commits. Later HEAD became `273a1c08b068e20f5cdbaef52f29a3a1c8d5fe23`. No source
edits from that work were overwritten. The baseline is a moving-worktree observation,
so before/after suite timing is not a fixed-commit causal comparison.

The slowest baseline case was
`test_resume_after_failed_step_keeps_requested_change_plan` (15.36s).
Review, API and contract workflows dominate; many take 6–10s. They exercise real
atomic filesystem writes, journalling and background delivery against fake providers.
Polling intervals are 10/20ms; durability calls remain real. We did not bypass fsync,
shorten real timeout guards, introduce parallel workers, or change product behavior.
Summed baseline JUnit case times: review integration 355.05s, API integration
160.23s, contracts 90.41s; all other owners together were much smaller.
Dependencies were already installed; setup/install time was not measured.
`gh run list` returned no EXD runs, so hosted CI timings are unavailable.

Existing skip: `tests/contract/test_contract_suite.py::test_restart_fails_open_runs`
explicitly defers restart support until S4.4. There were no baseline failures.

Discovery used `rg`/`rg --files --hidden --no-ignore-vcs` from this repository,
with scoped roots `src`, `tests`, `docs`, root contributor/config files, and Git
inventory of `.github`/`scripts` (neither existed originally). Relevant Python,
Markdown, YAML, JSON and manifests were included. Exclusions: `.git`, `.venv`,
`node_modules`, `__pycache__`, `.pytest_cache`, `.ruff_cache`, `dist`, `.worktrees`,
`.kilo`, runs/research/pilot evidence, `.env*` and local `exd.yaml`. An initial
glob-order mistake included cache filenames; the corrected scoped manifest excluded
them. A truncated import/AST console summary was treated as partial and supplemented
by scoped searches and a full machine inventory. No secrets or run evidence were read.

## Reviewed retention candidates

| Candidate | Decision and independent contract | History / remaining proof / risk |
|---|---|---|
| `tests/unit/api/test_dependencies.py::test_api_dependencies_available` | R: import smoke validates installed optional API/test dependencies, including uvicorn. | Added with API extra in `26297273`; API boundary suites additionally import FastAPI/jsonschema. No deletion seam or unique stronger uvicorn installation proof established; retain. |
| `tests/unit/api/test_dependencies.py::test_vendored_contract_is_pinned_to_one_management_commit` | R: provenance pin/SOURCE is distinct from model-schema agreement. | `26297273`; wire tests guard schemas, not commit pin syntax. Vendored refresh is an external contract boundary. |
| `tests/unit/packs/test_examples.py::test_agents_md_names_registry_rule` | R: documentation guard independently preserves the public scientific registry rule. | `7cbec9a7`; read complete file, AGENTS, registry consumers. No redundant independent rule guard proved; low cost, no removable production seam. |
| `tests/unit/packs/test_content.py` (three declarations) | R: externally loaded YAML/templates compose into supported scientific rubrics and registered calculators/validators. | `ead9879f`; read pack loader and callers. Static-looking assertions guard shipped input format/defaults; dynamic importorskip requires conservative pack/science roots. |
| `tests/contract/test_wire_models.py` (ten declarations) | R: vendored protocol version/model/schema/examples, unknown-field policy and secret serialization. | External protocol proof survives application refactors; retention bar applies. Assertions without explicit `assert` can still fail validation. |
| `tests/unit/benchmark/test_validate.py::test_no_non_200_response_is_ever_verified` | R: non-200 status, resource kind and plausible body combinations cannot become verified evidence. | Read complete suite, acquire/validate/HF caller chain and overlaps. Individual HTTP-client tests do not prove downstream scientific status. No deletion based on parameter count. |

There are no F/C/D changes, no retired layers, no removed test-only exports, and no
replacement coverage requiring consolidation mutations. All original owner suites
remain the keepers for their contracts. No unrelated product defects were fixed.

## Commands and shared selection policy

```powershell
# Deliberate edit check; choose the test boundary you are currently changing.
uv run python scripts/affected_tests.py --files tests/unit/api/test_observability.py --run
uv run python scripts/affected_tests.py --lane unit-api --run
# Safe automatic selection of all staged, unstaged and untracked changes.
uv run python scripts/affected_tests.py --run
# Pre-push / PR: the whole target-branch diff, not just the previous commit.
uv run python scripts/affected_tests.py --base origin/main --run
# Full handoff, main, release, schedule and manual checks.
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

`scripts/test-lanes.json` is the single machine-readable policy used by local and CI.
Owner lanes correspond to existing unit/integration/contract/CLI directories; commands
run exact selected files with `python -m pytest -q`, rather than every file in each
displayed owner. Explicit `--files`/`--lane` are deliberate edit scopes and do not
replace required full handoff checks. `--changed` supplies explicit paths for mapping
verification. The concrete graph test-file list controls CI; JSON lane names describe ownership and
manual scopes. A future graph-known test still runs if its display group is not yet
named; missing graph ownership falls back to full.
Without an explicit scope, Git diffs are unioned independently across
merge-base..HEAD, index and worktree so opposing edits cannot cancel selection.
Renames report both old/new paths with `--no-renames`; deleted paths select full.
Ordinary untracked paths are included; ignored untracked files under src/tests/scripts
and the tested docs/examples asset directory
also count, excluding the same cache/evidence/secret/config names above. The AST graph
reads Python under src/tests including ignored and hidden paths, with those exclusions.

AST reverse imports include nested imports, relative imports, from-submodules,
package initializers, conftest and dotted string patch/import targets. This is a
conservative repository resolver, not a general Python dependency analyzer. Dynamic
methodology assets and core contracts/runtime/persistence/science/workflow/config/
vocabulary always run full; so do shared test helpers, imported test modules,
fixtures, vendor contracts, source pack assets, docs examples, manifests/lock/build/
CI/tooling/config roots. A new/unmapped path, missing/deleted module, no known owner,
invalid AST, missing Git/base or diff error runs full. All full-root rules are listed
in the policy. Only `README.md`, this report and the ledger are explicitly ignored;
general Markdown is not ignored because examples/AGENTS/vendor docs are tested inputs.
When a new test starts reading an asset, update the shared policy in that change.
If a full main run finds a failure in an owner omitted on its PR, repair the map
alongside the defect and add a selection regression case.

## CI and gate

There was no tracked workflow. `.github/workflows/tests.yml` adds a minimal Ubuntu
Python3.12/uv pipeline: PR selection, exact test plan, Ruff lint/format, and an
always-running `gate`. The selection job emits an immutable JSON plan that the test
job consumes after setup, so build/untracked files cannot change selection afterward.
Full runs remain on main/master and release branch pushes, tags, published releases,
merge groups, schedule and manual dispatch. Slow contract/review cases remain on
affected PRs. No live-provider tests are enabled.
The tag pattern is `**` so names containing `/`, such as `release/v1.0`, also run full.

Configure branch protection to require **gate** once hosted checks have been observed.
Repository files cannot change hosting branch protection. The gate requires successful
selection and lint; test failure/cancellation/missing results fail. A skipped test job
passes only when successful selection explicitly says there are no tests. The workflow
was inspected locally; hosted Actions/setup/cache/install wall time is unverified.

## Verification

Automated tooling tests exercise real temporary Git repositories (fixture commits only,
signing and hooks disabled per invocation), relative/from imports, package initialization,
nested/patch-string imports, shared helpers/core/lock/CI, unknown/deleted paths, docs,
AST errors, staged+unstaged+untracked/rename/delete, ignored importable files and cache
exclusions, ignored tested example additions, invalid stored plans and gate
failure/cancellation/skips. The staged-shadow
regression first failed (empty selection), then passed after independent diff union.
An independent review also reproduced an ignored `docs/examples/grade7-reintroduced.md`
addition incorrectly producing an empty diff. Its real-Git regression failed first,
then passed after adding the tested examples directory to ignored-file discovery.
Explicit `--base ""` also first failed its regression by behaving like clean local
mode; testing `base is not None` now makes the missing target select full.

Actual mapping probes: standalone observability test -> one unit-api test file;
observability source -> 15 dependent API/contract/review/unit-api files;
shared providers/uv.lock/unknown source -> full; README -> empty; docs example -> full.
No changes to sibling repositories are included in these probes.

Mutation: verified `src/experiment_design/api/app.py` was clean, saved exact bytes,
replaced the service-key acceptance guard with `return False`, then ran
`uv run python scripts/affected_tests.py --changed src/experiment_design/api/app.py --run --fail-fast`.
Selection ran the boundary suite and `test_capabilities_valid` failed (1failed,4.68s),
because authenticated requests were rejected. A finally block restored SHA-256 identical
bytes; Git showed no mutation diff. The restored case passed (1passed,3.64s).

Final results: **816 passed, 1 existing skip in 591.10s** with the earlier 40 tooling
cases collected; latest focused tooling **42 passed in 18.50s** covers the two later
safety corrections. `uv run ruff check .` passed and `uv run ruff format --check .`
reported 135 files formatted. The machine was shared throughout; the full-suite
difference from 656.11s is not an attributed performance gain.

Exact final commands:

```powershell
uv run pytest -q --durations=30 --junitxml=$env:TEMP/exd-audit-final.xml
uv run pytest tests/tooling/test_affected_tests.py -q
uv run ruff check .
uv run ruff format --check .
```

One early final
attempt was interrupted after six cases to include review-requested selector regression
tests before the fresh full run; it is not reported as a completed check. The later
ignored-example and empty-base selector corrections are covered by the latest focused tooling run;
the in-flight full run had already collected the preceding 40 tooling cases.
