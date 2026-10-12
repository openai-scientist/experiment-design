# Test feedback lanes

Run commands from the repository root. Lanes are focused developer commands, not
an automated affected-test selector. No CI or type checker is configured.

The pre-cutover baseline was 411 passing tests in 70.49s. The first cleanup run
was 399 passing / 2 failing in 66.32s; failures were obsolete answer-command and
review expectations, repaired before final verification. Counts/timing describe
different suites; they are not a speed comparison.

| Edit scope | Focused command | Required broader check |
|---|---|---|
| Optional scientific calculators / validators | `uv run pytest tests/test_power.py tests/test_validators.py tests/test_computation_interpretation.py -q` | Full suite for numerical/schema/claim guards |
| S2 exploration contracts/agent | `uv run pytest tests/test_exploration_agent.py tests/test_exploration_contracts.py tests/test_exploration_store.py tests/test_exploration_workflow.py -q` | Workflow lane for framing/routing changes |
| S3–S7 workflow / authority / publication | `uv run pytest tests/test_experiment_plan.py tests/test_stage_pipeline.py tests/test_review.py tests/test_recovery_contracts.py tests/test_qualification.py tests/test_plan_review.py tests/test_publication.py -q` | Full suite for cross-stage/state changes |
| CLI / presentation | `uv run pytest tests/test_cli.py tests/test_exploration_cli.py tests/test_researcher_presentation.py -q` | Workflow lane for lifecycle command changes |
| Providers / task loop | `uv run pytest tests/test_llm.py tests/test_loop.py tests/test_jev.py tests/test_preview_schema.py -q` | Full suite for shared runtime changes |
| Historical export | `uv run pytest tests/test_output.py tests/test_exploration_output.py tests/test_exploration_compatibility.py -q` | Full suite for decoder/serializer changes |

Shared roots (contracts, Store, package/patch, workflow, config, shared test support,
fixtures, pyproject.toml and uv.lock), multiple scopes or an unmatched change:
`uv run pytest -q`. Use this full command before handoff, merge or release;
focused green results do not substitute for it. Run Ruff check and format check too.
No hooks, selective CI coverage or new test dependencies were added.

Reusable domain fixtures and fake external providers live in tests/support/;
test modules do not import helpers from other test modules. Four new behavior
functions in test_experiment_plan.py cover integrated selection, rejected-direction
redesign, frozen three-file consistency and corrupt/different destination rejection.
Existing qualification cases cover scientific gaps/early decisions, domain-agnostic
plans without internal calculators, approval and evidence invalidation. Numerical
reference, provenance, cohort isolation, storage/recovery and historical guards stay.
Strict template parsing, chain quotas and Handoff Agent tests were removed with
their production behavior; keepers now test actual scientific/workflow contracts.

Live ARC comparison is prepared separately in
[the evaluation protocol](docs/superpowers/evaluations/2026-10-12-exp-plan-arc-evaluation.md).
It needs explicit authorization, fresh provider calls and deterministic prerequisites.
No live evaluation or successful downstream execution is claimed by this refactor.
