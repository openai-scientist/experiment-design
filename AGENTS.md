# Experiment Design

## Scope

Produce an independently reviewed, researcher-readable Experiment Plan from exact
Markdown inputs. Empirical execution is separate. Preserve all researcher inputs,
historical exports, pilot files and run evidence; do not migrate or
reinterpret historical SQLite runs as the active filesystem workflow.

## Commands

Python 3.12+, uv, PowerShell, repository root:

- Dependencies: `uv sync --locked`
- CLI: `uv run exd --help`
- Full deterministic checks (parallel, ~2 min): `uv run --with pytest-xdist pytest -q -p no:cacheprovider -n 8`; never run the full suite serially
- Focused publication checks: `uv run pytest tests/e2e tests/integration/publication -q`
- Style: `uv run ruff check .` and `uv run ruff format --check .`

Configure ignored `exd.yaml` from `exd.example.yaml`; provide its named environment
variables without exposing secrets. `run <input.md> [additional.md ...]` starts the
single active seven-stage workflow under `runs_root`. `resume <run-directory>`
reuses valid completed stages and cumulative usage. `status`, `review`, `decide`
and `export` work offline without provider keys. A decision records real text,
researcher identity and `--revision` equal to the current attempt. A pending question
carries 2-4 numbered options; answer with `decide --action ANSWER --option N [--text ...]`
or `--text` alone; the record stores the exact option text and the free text. Explicit run
budget increases require `--llm-token-limit` and a nonblank `--reason`; ordinary
resume preserves the pinned ceiling, consumed tokens and unknown-usage stops.

## Ownership and workflow

Small components with direct control flow; reuse existing utilities. Contracts
hold small typed runtime/review/calculation records, not a structured scientific
plan body. Persistence owns atomic filesystem artifacts, manifests, checkpoints,
source snapshots and append-only events; it imports no providers or workflow.
Agents are bounded workspace agents: each gets a goal and an index of role-scoped
documents, then uses read, search, source and (design, methodologist, revision only)
calculate before submit. Drafting roles also get the full text of their required
documents up front; review roles read them themselves and run check before submit.
They own bounded scientific reasoning and specialist tools. Science owns the
existing supported statistical algorithms. Workflow owns stages and legal routes.
Publication copies exact reviewed Markdown bytes without model calls or rendering.
Profiled runs extract a separate experiment index after design and revision, check it
before final review and copy the matching index at publication; the reviewed plan bytes stay exact.
CLI/config own researcher commands and configuration.

| Stage | Responsibility |
|---|---|
| S1 | Snapshot exact immutable source text and provenance. |
| S2 | Frame the question (one or several hypotheses), compare directions and expose actual researcher questions. |
| S3 | Write a grounded scientific plan with protocol, measurements and analysis. Profiled runs first build `resources.json` (step `s2b_resources`) and wait for the researcher to review it. |
| S4 | Independent Methodologist/Skeptic reviews; bounded revision or reframe. |
| S5 | Fresh check of the current plan, source/replies and objection handling. |
| S6 | Present the exact current Markdown and record real researcher decisions. |
| S7 | Publish `stage-07/exp_plan.md` without changing the scientific body. |

Code enumerates the legal routes and picks one when a rule decides it: a sole option,
a needed question, a progress route (DESIGN, CHECK, PRESENT) while no MAJOR fix is open,
or a single legal refinement; a defect found only by a code check is revised. JEV only
chooses REVISE or REFRAME when both are legal. STOP is legal only when no other route is;
`forced` records whether limits removed the refinements. A review stopped by its budget is
never presented as complete. Agents choose HOW.
Validate returned options; never claim JEV itself is deterministic. Rule-decided
routes are labelled automatic and require no fabricated JEV call. Methodology is
open-ended; calculator coverage is not an allowlist or universal power gate. A method
without a calculator can still be proposed, with its uncertainty stated.
Keep TypeSafe/JEV provider dependency and public configuration.

## Authority and scientific honesty

Supplied hypotheses/constraints and actual confirmed replies are authoritative. One input
may state several hypotheses; the single plan covers each with its own claim and decision
rule, and only the researcher excludes or defers one. An ANSWER to a question raised
at S4/S5 resumes at revision with that stage's objections, followed by fresh rejoinders and
final check; questions from S2/S3 restart at framing.
Agent proposals and clarification questions are not confirmed researcher intent.
Record exact replies, identity, UTC time, current attempt and relevant document or
source/question bindings. Stale replies/reviews cannot bless a revised document.
Independent critics receive the same draft with fresh work and no pending peer
results. Final checking receives the revised document, original objections and a code-computed
unified diff of the revision. After each revision both critics record, per objection,
resolved, rebuttal accepted or maintained with grounds; maintained ones go to the final
check. Evidence search covers supplied documents only.

Never invent evidence, numerical computations, answers, acceptance or superiority.
Statistics are computed only by calculators in `science/registry.py` enabled by the active
pack. Runs without a selected profile retain the legacy calculators.
Each calculator needs a test against independent reference values. Do not add mixed-model power.
Computation findings cite accepted own-task tool evidence; prior-task support does
not become a reviewer's own computation. Source spans must exist in supplied text.

Manifest completion follows atomic artifact writes. Validate artifact and input
checksums/dependencies before reuse; checkpoint alone cannot establish completion.
Archive affected stages to attempts before revision while preserving cumulative
usage and actual response events. Checksums prove identity, not scientific truth.
An LLM attempt with unknown usage (timeout, connection error, 408, 5xx, 2xx without usage)
whose identical retry succeeds is charged a ceiling: the retry's input tokens plus
`max_tokens`. Unknown usage without such a bound stops the run permanently; 4xx
rejections count zero tokens. JEV usage may be unknown and
is bounded by the JEV call limit. Cached input counts; reserve fresh final-check allowance.
Default limits: 1000000 LLM tokens, 24 JEV calls, two total refinements/reframes,
12 turns and 120000 tokens per specialist. A specialist at its token or turn ceiling
continues only after the researcher raises `task_max_tokens`/`task_max_turns` in
`exd.yaml` and resumes.

Reviewed scientific completeness and researcher acceptance are distinct. Pending
acceptance alone permits publication. Incomplete drafts require explicit
`--allow-draft` and disclosed gaps. Material unresolved defects, failed calls or
budget stops cannot produce complete/approved flags. Publication is neither
researcher acceptance nor execution permission. Markdown remains exact; status,
approval and gaps belong in publication metadata and researcher view headers.

## Working agreements and verification

Use `rg`/`rg --files`; never read secrets or `.env`. Keep typed public APIs, domain names
and Ruff. Never invoke Astra without explicit user request. No unauthorized
commit/push/reset/stash/branch changes.

Tests use fake external providers and independent calculator references. Shared
contracts/runtime/storage/science/workflow changes require full deterministic tests;
before handoff run full tests, lint and format checks. Mutation-check changed
authority/scientific guards, then restore exactly.

During edits, use `uv run python scripts/affected_tests.py --files tests/path/test_file.py --run`
or `--lane unit-api --run` for a deliberate focused check. Automatic local checks use
`uv run python scripts/affected_tests.py --run`; pre-push uses
`uv run python scripts/affected_tests.py --base origin/main --run`. Omit `--run` to
inspect the selection. The shared policy is `scripts/test-lanes.json`: shared core,
fixtures/helpers, unknown/deleted paths and unavailable Git evidence run everything.
Focused checks support the edit loop; the full handoff checks above remain required.
See `docs/testing.md` for ownership, selection policy and CI gate behavior.

Regenerate the statistics fixture with
`uv run --with statsmodels tests/fixtures/stats_reference.py`.
`tests/unit/science/test_stats.py` checks the generator SHA-256 in
`tests/fixtures/stats_reference.json`, so edit the generator and fixture together.

## Service and contract

`exd-api` (extra `api`) serves the Platform engine contract from `api/contract_models.py`.
`api/runs.py` creates one engine run per `stage_run_id`, drives the same seven-stage
workflow on the filesystem store, and publishes `stage-07/exp_plan.md` as the
`exd.exp_plan/v1` artifact. The plan review at stage 6 opens a gate (`api/mapping.py`); the service
sends events and telemetry to Platform through `api/callbacks.py`.
Management owns the contract. Do not edit `tests/contract/vendor/`. To refresh it, copy
`schema/`, `examples/` and README from management `docs/contract` and write the management
commit and contract version into `vendor/SOURCE`. `vendor/README.md` describes management's
repo: its `scripts/export_contract.py` and `tests/contracts` do not exist here. Check with
`uv run pytest tests/contract -q`.

Live verification requires explicit authorization and passing deterministic
prerequisites: production CLI, empty new Store, fresh provider calls and exactly
the specified inputs. Never inject answers or force state. Record command, input,
run ID, stopping state and artifacts.
