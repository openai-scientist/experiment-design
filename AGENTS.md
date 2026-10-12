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
| Focused check | `uv run pytest tests/test_<module>.py -q` |
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

## Code ownership

Under `src/experiment_design/`:

- `contracts.py`, `stages/`: records, stage contracts and explicit stage owners.
- `package.py`, `patch.py`: source intake and protected design changes.
- `workflow.py`, `review.py`, `qualification.py`, `decisions.py`: coordination,
  independent review, plan qualification and researcher decisions.
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

## Verification and completion

Tests protect statistical correctness, authority, provenance, storage, commands or
transitions. Fake external services only; avoid self-derived expectations, vacuous
assertions and duplicates. Mutation-check changed scientific/authority guards,
confirm failure, restore exactly and inspect the diff.

Use focused checks while editing; run full tests for shared contracts, Store,
science or workflow changes. Before code commit/handoff, run full tests, lint and
format checks. Documentation-only edits need path/command/diff verification.
Use the focused lane commands in TESTING.md; shared contracts/runtime/Store and
unmatched changes require the full suite. Retain full coverage before handoff.
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
