# Experiment Design

## Purpose and scope

Produce a grounded, independently reviewed Experiment Plan from researcher Markdown.
Disclose assumptions and limitations; downstream implementation/execution is separate.

## Setup and commands

Python 3.12+, `uv`, PowerShell; commands run from the repository root.

| Task | Command |
|---|---|
| Install locked dependencies | `uv sync --locked` |
| CLI usage | `uv run exd --help` |
| Focused check (review example) | `uv run pytest tests/integration/review -q` |
| Complete deterministic suite | `uv run pytest -q` |
| Lint / formatting | `uv run ruff check .` / `uv run ruff format --check .` |
| Measure test time | `uv run pytest -q --durations=15` |

Configure ignored `exd.yaml` from `exd.example.yaml`; supply its named environment
variables without exposing secrets. Start with
`uv run exd --config exd.yaml run <input.md>`; use `--help` for other commands.
`uv run pytest -m live -q` selects live-marked tests; zero collected tests prove
nothing. Default tests exclude live. No type checker or CI workflow is configured.

Exploration is a capability of S2 inside the canonical `run <input.md>` workflow
(additional Markdown inputs are allowed). It proposes, compares and selects
scientific directions before synthesis; source-fixed methodology preserves its
authority. Internal selection is a proposal, never researcher confirmation.
`status` and `review` inspect context/candidates offline before a design exists;
`resume` reuses accepted tasks and selections with cumulative usage. Historical
standalone exploration is read/export only. New runs do not have a separate
exploration lifecycle or final candidate-only export. Empirical execution is out of scope.

At final review, `decide --action CLARIFY --text <question>` records a question;
`resume` obtains a bound explanation from the existing Design Agent. It leaves
researcher approval pending and does not revise scientific choices. Answers remain
in the decision history and publication; clarification questions are not confirmed intent.

## Components

Under `src/experiment_design/`:

| Component | Responsibility |
|---|---|
| `contracts.py` | Shared records, transitions and errors. |
| `design/` | Source intake, protected changes, directions, approval policy and transactional researcher decisions. |
| `persistence/` | Authoritative Store, atomic SQLite updates, journal and snapshots. |
| `runtime/` | Provider calls, task execution and usage budgets. |
| `agents/` | Scientific reasoning and specialist tool use. |
| `science/` | Statistical computation, validation and evidence. |
| `workflow/` | Stage coordination, independent review and qualification. |
| `publication/` | Plan rendering, frozen exports and isolated historical readers. |
| `cli.py`, `config.py` | Researcher commands and configuration. |

Keep dependencies acyclic. Pure design policies never import services;
`design.decisions` may use persistence, but persistence imports only shared
contracts and pure design policies, never workflow or agents. Agents never import
workflow execution. Publication consumes frozen records without invoking workflow
or agents. Stage owners live in `workflow/stages/`; public workflow APIs are
exported by `workflow/__init__.py`.

Tests use `unit/`, `integration/` and `e2e/`, grouped by behavior, not source files.
Shared builders/fake external providers live in `tests/support/`; test modules do
not import one another. `tests/fixtures/` holds reusable references.
`docs/`, `pilot/`, `.exd/`, `exd.yaml`
and `.env` are ignored/local; deterministic checks must not require them.
Preserve researcher inputs and run evidence.

## Working agreements

- Inspect code, callers, tests and Git changes; preserve others' work. Plan complex
  changes; implement clear, authorized changes directly.
- Keep components small, with one clear responsibility and direct control flow.
  Extend the existing owner first; add a module or abstraction only when a concrete
  requirement needs it. Avoid extra layers, generic frameworks, speculative
  abstractions, dead code, placeholders and unrequested architecture changes.
- Reuse code and keep fixes scoped. Use domain names, typed public functions and
  Ruff; keep task/plan numbers out of code.
- Delegate only when requested or required by applicable instructions, with bounded
  context, disjoint ownership and verified results. Never invoke/delegate to Astra
  unless the user explicitly requests it.
- Keep Git operations authorized; commit subjects use `type: concise description`.
  Maintain durable rules here, not task history.

## Scientific and workflow invariants

- JEV chooses WHAT/NEXT among legal code-enumerated options; agents choose HOW.
  Code validates transitions and computes statistics.
- SQLite is authoritative; the append-only journal is the event source. Design
  intake owns source reads, Store owns state, publication owns outputs;
  CLI/config load configuration.
  Persist meaningful stage outputs with version/input references.
- One canonical design per version; after synthesis, use validated `apply_patch`.
  Protect source/confirmed intent, measurement mirrors, context values and typed
  commitments. Researcher-owned fields change through recorded answers only;
  reconcile new evidence and changed claims before plan acceptance.
- Statistics come from `science/power.py`; missing/unsupported inputs yield
  `not_computed` with reasons. Agent-supplied computed inputs are unsupported
  without attestation. Never invent evidence, power, verification, answers or approval.
- Tools use committed designs, sensitivity overrides or explicit previews. Findings
  cite accepted computations from their own task via exact `evidence_ref`.
  Previews cannot authorize canonical claims. Keep fakes out of production.
- Independent review/verification uses fresh provider work, not replayed answers.
  Preserve frozen cohort barriers and version/context bindings.
- Keep schemas/message prefixes stable within tasks. Journal provider attempts,
  unknown usage, cache telemetry and budget-stop scope/reason. Cached tokens count.
  Reserve independent review/verifier allowance; authorized increases preserve cumulative usage.

| Stage | Required responsibility |
|---|---|
| S1 | Capture exact immutable source text and provenance. |
| S2 | Ground context, explore/compare/select directions; expose uncertainty and real decisions. |
| S3 | Synthesize or redesign the selected direction as a protected canonical Design. |
| S4 | Independent Methodologist/Skeptic critique; grounded refinement or reject → S2; fresh checks. |
| S5 | Assess reviewed plan completeness; route actionable feedback to S2/S3/S4 or researcher. |
| S6 | Record real decisions; final review may remain pending. |
| S7 | Publish exp_plan.md, exp_plan.json and manifest.json from one frozen snapshot. |

Methodology is open-ended scientific reasoning, never an allowlist. Optional tools
use explicit calculator_id; unknown/unsupported coverage does not select or reject
methodology. Do not create new scientific computation implementations in this cutover.
Missing core intent requires an actual ResearcherDecision; proposals are not answers.

Qualification aggregates current Design, independent review/dispositions, decisions
and relevant bound evidence. plan_complete describes scientific quality/completeness,
not successful execution. No Handoff Agent or internal handoff evaluation. Minor
uncertainty is disclosed; material gaps can return to their stage owner using existing
limits. Keep STOP for technical, integrity or interpretability failures.
Researcher acceptance requires current qualification and reconciled scientific
choices. Numerical claims must have supported accepted evidence when supplied;
power calculation is not a universal gate. Record exact response revision, identity
and timestamp, including final review. Requalify after relevant evidence/issues change.
Publish frozen supporting snapshots without recomputation. Schema-1, pre-cutover
schema-2 and standalone exploration support historical reads/exports only through
isolated compatibility code. Internal stage artifacts remain in SQLite, not extra
active final files.
Primary Markdown includes researcher constraints/budget from frozen context.
Manifest publication/approval status is derived from the same snapshot as JSON.
Verifier receives accepted rebuttal support with original task bindings; prior-task
observations never become its own-task computation evidence.

## Verification and completion

Tests protect statistical correctness, authority, provenance, storage, commands or
transitions. Fake external services only; avoid self-derived expectations, vacuous
assertions and duplicates. Mutation-check changed scientific/authority guards,
confirm failure, restore exactly and inspect the diff.

Use focused checks while editing; run full tests for shared contracts, Store,
science or workflow changes. Before code commit/handoff, run full tests, lint and
format checks. Documentation-only edits need path/command/diff verification.
Use focused behavior groups under `tests/unit/`, `tests/integration/` or `tests/e2e/`;
shared contracts/runtime/Store and unmatched changes require the full suite.
Retain full coverage before handoff.
Report changes, actual results and limitations using PASS/FAIL/INCONCLUSIVE.
Never claim unperformed checks or researcher acceptance.

## Code Review Rules

Flag fabricated authority, lost independent review, stale bindings, non-atomic
journal updates, unsupported results and execution approval inferred from drafts.
Check public-boundary regressions and scope; Ruff handles style.

## Fresh live verification

Use production CLI, an empty Store, fresh calls and exactly the specified inputs.
Historical runs are comparison evidence. Surface missing-input decisions; record
real replies through production lifecycle. Never inject answers or force state.
Report command, inputs, run ID, stopping state and artifacts. Live requires
authorization and passing deterministic prerequisites.
