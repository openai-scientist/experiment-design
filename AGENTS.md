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
- Full deterministic checks: `uv run pytest -q`
- Focused publication checks: `uv run pytest tests/e2e tests/integration/publication -q`
- Style: `uv run ruff check .` and `uv run ruff format --check .`

Configure ignored `exd.yaml` from `exd.example.yaml`; provide its named environment
variables without exposing secrets. `run <input.md> [additional.md ...]` starts the
single active seven-stage workflow under `runs_root`. `resume <run-directory>`
reuses valid completed stages and cumulative usage. `status`, `review`, `decide`
and `export` work offline without provider keys. A decision records real text,
researcher identity and `--revision` equal to the current attempt. Explicit run
budget increases require `--llm-token-limit` and a nonblank `--reason`; ordinary
resume preserves the pinned ceiling, consumed tokens and unknown-usage stops.

## Ownership and workflow

Small components with direct control flow; reuse existing utilities. Contracts
hold small typed runtime/review/calculation records, not a structured scientific
plan body. Persistence owns atomic filesystem artifacts, manifests, checkpoints,
source snapshots and append-only events; it imports no providers or workflow.
Agents are bounded workspace agents: each gets a goal and an index of role-scoped
documents, then uses read, search, source, calculate and check before submit.
They own bounded scientific reasoning and specialist tools. Science owns the
existing supported statistical algorithms. Workflow owns stages and legal routes.
Publication copies exact reviewed Markdown bytes without model calls or rendering.
CLI/config own researcher commands and configuration.

| Stage | Responsibility |
|---|---|
| S1 | Snapshot exact immutable source text and provenance. |
| S2 | Frame the question (one or several hypotheses), compare directions and expose actual researcher questions. |
| S3 | Write a grounded scientific plan with protocol, measurements and analysis. |
| S4 | Independent Methodologist/Skeptic reviews; bounded revision or reframe. |
| S5 | Fresh check of the current plan, source/replies and objection handling. |
| S6 | Present the exact current Markdown and record real researcher decisions. |
| S7 | Publish `stage-07/exp_plan.md` without changing the scientific body. |

JEV chooses WHAT/NEXT among legal code-enumerated options; agents choose HOW.
Validate returned options; never claim JEV itself is deterministic. Sole legal
routes are labelled automatic and require no fabricated JEV call. Methodology is
open-ended; calculator coverage is not an allowlist or universal power gate.
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
results. Final checking receives the revised document and original objections.

Never invent evidence, numerical computations, answers, acceptance or superiority.
Statistics come from the supported independent-pooled-t/equal-cluster-t calculators
with existing applicability/provenance limitations; do not add mixed-model power.
Unsupported methods remain scientifically available with honest uncertainty.
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

Inspect surrounding code, callers, tests and Git changes; preserve others' work.
Use `rg`/`rg --files`; broad discovery includes hidden/ignored source with explicit
exclusions for Git/dependencies/caches/builds/unrelated worktrees. Report actual
scope and exclusions; do not read secrets or `.env` during discovery.
Keep fixes scoped, typed public APIs, domain names and Ruff. Delegate only when
requested/required with disjoint ownership; never invoke Astra without explicit
user request. No unauthorized commit/push/reset/stash/branch changes.

Tests guard real scientific/authority/provenance/storage/command contracts using
fake external providers and independent calculator references. Run focused checks
while editing; shared contracts/runtime/storage/science/workflow changes require
full deterministic tests. Before handoff run full tests, lint and format checks.
Mutation-check changed authority/scientific guards, confirm failure and restore
exactly. Report PASS/FAIL/INCONCLUSIVE with actual commands and limitations.

Live verification requires explicit authorization and passing deterministic
prerequisites: production CLI, empty new Store, fresh provider calls and exactly
the specified inputs. Surface missing-input decisions; never inject answers or
force state. Record command/input/run ID/stopping state/artifacts.
