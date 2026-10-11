# Experiment Design

## Purpose and scope

Produce a grounded, reviewable EDP from researcher Markdown; disclose assumptions
and limitations. Draft completion is separate from execution readiness.

## Setup and commands

Python 3.12+, `uv`, PowerShell; commands run from the repository root.

| Task | Command |
|---|---|
| Install locked dependencies | `uv sync --locked` |
| CLI usage | `uv run exd --help` |
| Focused check | `uv run pytest tests/test_<module>.py -q` |
| Complete deterministic suite | `uv run pytest -q` |
| Lint / formatting | `uv run ruff check .` / `uv run ruff format --check .` |
| Measure test time | `uv run pytest -q --durations=15` |

Configure ignored `exd.yaml` from `exd.example.yaml`; supply its named environment
variables without exposing secrets. Start with
`uv run exd --config exd.yaml run <input.md>`; use `--help` for other commands.
`uv run pytest -m live -q` selects live-marked tests; zero collected tests prove
nothing. Default tests exclude live. No type checker or CI workflow is configured.

## Code ownership

Under `src/experiment_design/`:

- `contracts.py`, `stages.py`: records and stage contracts.
- `package.py`, `patch.py`: source intake and protected design changes.
- `workflow.py`, `review.py`, `qualification.py`, `decisions.py`: coordination,
  independent review, readiness and researcher decisions.
- `runtime/`, `agents/`, `science/`: Store/provider loop, role tools and statistics.
- `output.py`, `cli.py`: exports and researcher commands.

`tests/fixtures/` holds reusable references. `docs/`, `pilot/`, `.exd/`, `exd.yaml`
and `.env` are ignored/local; deterministic checks must not require them.
Preserve researcher inputs and run evidence.

## Working agreements

- Inspect code, callers, tests and Git changes; preserve others' work. Plan complex
  changes; implement clear, authorized changes directly.
- Reuse code and keep fixes scoped. Avoid speculative abstractions, dead code,
  placeholders and unrequested frameworks/architecture changes. Use domain names,
  typed public functions and Ruff; keep task/plan numbers out of code.
- Delegate only when requested or required by applicable instructions, with bounded
  context, disjoint ownership and verified results. Never invoke/delegate to Astra
  unless the user explicitly requests it.
- Keep Git operations authorized; commit subjects use `type: concise description`.
  Maintain durable rules here, not task history.

## Scientific and workflow invariants

- JEV chooses WHAT/NEXT among legal code-enumerated options; agents choose HOW.
  Code validates transitions and computes statistics.
- SQLite is authoritative; the append-only journal is the event source. Package
  owns intake reads, Store owns state, export owns outputs; CLI/config load config.
  Persist meaningful stage outputs with version/input references.
- One canonical design per version; after synthesis, use validated `apply_patch`.
  Protect source/confirmed intent, measurement mirrors, context values and typed
  commitments. Researcher-owned fields change through recorded answers only;
  reconcile new evidence and changed claims before execution readiness.
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
  Reserve verifier/handoff allowance; authorized increases preserve cumulative usage.

| Stage | Required responsibility |
|---|---|
| S1 | Capture exact immutable source text and provenance. |
| S2 | Ground context; expose unknowns and researcher decisions. |
| S3 | Synthesize canonical design, SAP, protocol and assumptions. |
| S4 | Complete independent review; refine and disclose limitations. |
| S5 | Separate reviewability, handoff completeness and execution readiness. |
| S6 | Record real decisions; final review may remain pending. |
| S7 | Publish a consistent draft/approved EDP and stage artifacts. |

Qualification records blockers; keep STOP for technical, integrity or interpretability
failures. Complete handoff requires inspecting the bound design, protocol and
stopping policies.
Draft export never authorizes execution; approval requires current qualification,
supported bound calculations and reconciled scientific decisions. Record exact
response revision, identity and timestamp through ResearcherDecision, including
final review. Requalify after evidence/issues change. Publish from frozen supporting
snapshots and bound observations, without recomputation. Schema-1 supports historical
reads/exports only; preserve its scientific meaning. Materialize stage Markdown/JSON
from SQLite alongside the EDP.

## Verification and completion

Tests protect statistical correctness, authority, provenance, storage, commands or
transitions. Fake external services only; avoid self-derived expectations, vacuous
assertions and duplicates. Mutation-check changed scientific/authority guards,
confirm failure, restore exactly and inspect the diff.

Use focused checks while editing; run full tests for shared contracts, Store,
science or workflow changes. Before code commit/handoff, run full tests, lint and
format checks. Documentation-only edits need path/command/diff verification.
Add lanes only above 60 seconds measured suite time; retain full coverage.
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
