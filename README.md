# Experiment Design

Experiment Design (`exd`) turns researcher hypotheses written in Markdown into an
independently reviewed, researcher-readable experiment plan. A run has seven stages:
source snapshot, framing, plan, independent review, fresh check, researcher decision
and publication. It does not run experiments. Rules for contributors are in
[AGENTS.md](AGENTS.md).

## Install

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```
uv sync --locked
uv sync --locked --extra api   # also installs the service dependencies
```

## Configure

- Copy `exd.example.yaml` to `exd.yaml` (ignored by Git). Use `--config PATH` to pick another file.
- `EXD_LLM_API_KEY`: key for the LLM endpoint in `llm.base_url`.
- `TYPESAFE_API_KEY`: key for the TypeSafe/JEV router.
- The CLI loads a `.env` file (see `.env.example`) before it calls a provider.

The names of the key variables come from `api_key_env` in `exd.yaml`. Default limits
(tokens, JEV calls, refinements, turns) are in the `run` section.

## Input format

A Markdown file with a heading `## H<n>: <claim>` for each hypothesis. Use at least two.
Do not add `## Constraints` or `## Budget` sections. Examples:
[hypotheses.example.md](hypotheses.example.md) and
[hypotheses.ml.example.md](hypotheses.ml.example.md). The first file is the hypothesis
file; more Markdown files can follow as extra sources.

## Use the CLI

```
uv run exd run hypotheses.example.md
uv run exd status <run-id>
```

| Command | Purpose |
|---|---|
| `run <input.md> [more.md ...]` | Start a run. Calls the configured providers. |
| `resume <run>` | Continue a run. `--llm-token-limit N --reason TEXT` raises the budget. |
| `status <run>` | Show the stage, pending question and current attempt. Offline. |
| `review <run>` | Show the plan for review. Offline. |
| `decide <run> --action A --by NAME --revision N` | Record a researcher decision. `A` is `ANSWER`, `APPROVE`, `REQUEST_CHANGES`, `CLARIFY` or `REJECT`. Also `--text` and `--option`. Offline. |
| `export <run> --out DIR` | Copy the plan out. `--allow-draft` exports an incomplete draft. Offline. |

`<run>` is a run directory or an ID under `runs_root`.

## Output

Each run is a directory `runs/<run-id>/` with one folder per stage (`stage-01/` to
`stage-07/`). The plan is `stage-07/exp_plan.md`; its status, approval and gaps are in
`stage-07/publication.json`. Archived earlier attempts are in `attempts/`.
Examples of finished plans: [docs/examples](docs/examples).

## Platform service

`exd-api` serves the research-engine HTTP contract for the Platform.

```
uv sync --locked --extra api
EXD_SERVICE_KEY=<secret> uv run exd-api
```

| Variable | Meaning |
|---|---|
| `EXD_SERVICE_KEY` | Required. Clients send it in the `X-Service-Key` header. |
| `EXD_CONFIG` | Path to the config file. Default `exd.yaml`. |
| `EXD_PORT` | Listen port. Default `8000`. |
| `GITHUB_TOKEN` | Optional. Raises the GitHub API rate limit when verifying GitHub-hosted resources. |

Routes (`src/experiment_design/api/contract_models.py`): `GET /health`,
`GET /capabilities`, `POST /runs`, `GET /runs/{id}`, `GET /runs/{id}/events`,
`POST /runs/{id}/gates/{gate_id}`, `POST /runs/{id}/pause`, `/resume` and `/cancel`.
The management repository owns the contract. A copy of its schemas and examples is in
`tests/contract/vendor/`; `vendor/SOURCE` names the management commit.

Inputs: `i2h.hypotheses/v1` (required), `i2h.evidence/v1` and `review.findings/v1` (optional).
Evidence cards are rendered under the hypotheses that cite them. Blocking and major review
findings on the selected hypotheses become a "Known issues" list. A malformed optional input is
skipped with a warning. After a researcher or system change request, the final check must
resolve each earlier MAJOR finding and may raise a new MAJOR only on a section the revision
changed. Findings about evidence that exists only at execution time never block a plan.

## Test

```
uv run pytest -q          # deterministic tests, no network
uv run pytest -m live     # opt-in; calls real providers and needs the keys above
uv run ruff check .
```
