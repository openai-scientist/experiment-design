# Experiment Design

## Purpose
Turn researcher Markdown into a source snapshot and provenance-aware research
context, then an experiment design through seven owned stages, independent
review and qualification, and explicit researcher decisions. Export
a readable EDP and authoritative JSON from the same canonical design version.

## Layout
- `contracts.py`: scientific and workflow records.
- `config.py`: YAML configuration and environment variable names.
- `package.py`: exact Markdown intake and referenced source snapshots.
- `patch.py`: validated design changes, ownership, diff and hash.
- `stages.py`: seven responsibilities and permitted returns.
- `workflow.py`: stage progression and legal JEV coordination.
- `decisions.py`: researcher response, authorized application and reconciliation.
- `review.py`: independent cohort barrier, patches and verification.
- `qualification.py`: version-bound readiness and exact approval.
- `output.py`: human-first rendering and versioned export.
- `cli.py`: run, resume, status, review, decide, approve and export.
- `runtime/store.py`: SQLite records and append-only journal.
- `runtime/llm.py`: OpenAI-compatible tool-calling client.
- `runtime/jev.py`: TypeSafe workflow judge.
- `runtime/loop.py`: shared agent tool loop.
- `science/power.py`: statistical calculations and unit normalization.
- `science/validators.py`: deterministic scientific checks.
- `science/evidence.py`: source-backed keyword lookup.
- `agents/tools.py`: domain tools; role modules define distinct submit contracts.
- `tests/`: service fakes and behavioral contracts.
- `pilot/`: researcher-reviewed case and scorecard.

## Verify
- Fast: `uv run pytest -q`.
- Before commit: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`.
- Edit loop: `uv run pytest tests/test_<module>.py -q`.
- Live: `uv run pytest -m live -q` (requires real provider keys).
The default lane excludes `live`. The marker set in pyproject.toml is the lane map.
See [local testing](docs/testing.md) for owner commands and shared-change checks.

## Architecture rules
JEV selects workflow WHAT/NEXT among legal options. Code enumerates and validates
options and computes statistics. Agents choose HOW within their allowed tools.
Keep one canonical design per version; after the initial design, use apply_patch.
Researcher-owned claim fields change only through researcher answers.
New researcher evidence and changed claims require reconciliation before readiness.
Agent-supplied quantities cannot claim computed provenance without an attested
production calculation; such inputs are currently unsupported.
All statistical numbers come from science/power.py. Missing or unsupported inputs
produce not_computed with reasons. Keep fakes out of production. The journal is
the only event source. Input, state and output file access belongs to package,
Store and export respectively; config and CLI may load configuration.
Never use or delegate to Astra models unless the user explicitly requests Astra.

After the initial design, scientific tools use the committed version and accept
sensitivity overrides or patch previews. Tool observations expose an exact
evidence_ref; computation findings cite an accepted call from the current task.
Keep tool schemas and earlier messages stable within an agent task for provider
prefix caching. Record reported cache hits separately from missing cache telemetry;
cached input still counts toward token budgets. Do not replay cached LLM answers
for independent review or verification. Budget stops record their scope and reason
in the journal and appear in CLI status. Reserve verifier and handoff allowance
before optional coordination. Explicit resume budget increases preserve used tokens.
Schema-1 runs support historical reads/exports only; never silently migrate their
scientific meaning. Qualification and publication use bound observations, never
recompute numbers while rendering. Researcher responses record exact revision,
identity and timestamp; final review uses the same ResearcherDecision lifecycle.
Preserve confirmed context values and source/confirmed scientific commitments,
including their measurement mirrors. Typed path annotations carry commitment values.
Complete handoff requires inspecting the bound design, an execution protocol and
stopping policies. Requalify after supporting evidence or issues change; present
final review against the current assessment. Historical exports use frozen supporting
snapshots. Journal provider attempts so unknown usage remains visible after failure.

## Testing rules
Each test protects a user-visible or downstream contract: statistical references,
guards, journal/storage, commands or transitions. No assertion-free, duplicate,
self-comparing, constant-restating, framework-only or private-helper tests.
Fakes replace external services only. Mutation-check every guard: flip the source
condition, see the test fail, restore exactly and inspect git diff.
Measure with `uv run pytest --durations=15`. Add blast-radius lanes only if the
fast suite exceeds 60 seconds, following the test-audit skill.

## Code rules
Use domain terms in code, comments, docstrings, tests and commits; do not reference
plans, task numbers or spec section numbers. No dead code, TODO markers, empty
abstractions or speculative options. Prefer small modules and typed public
functions. Update this guide when commands or rules change.
