# Researcher-reviewed pilot

The core accepts one or more researcher Markdown files without prescribed headings.
S1 preserves exact supplied text and reachable local text attachments; S2 records
scientific understanding, provenance, contradictions and unresolved decisions.
The seven responsibilities and their evidence are described in
[stage responsibility](stage-responsibility.md). See [paired evaluation](paired-evaluation.md)
for the frozen baseline comparison and acceptance requirements.

Install with `uv sync`, copy `exd.example.yaml` to an ignored local configuration,
and set the configured LLM and TypeSafe key environment variables. YAML contains
variable names, never secrets. Commands that read artifacts, record researcher
responses, approve or export do not need provider credentials.

```text
uv run exd --config pilot/runs/<date>/config.yaml run <source.md> <additional.md>
uv run exd --config pilot/runs/<date>/config.yaml status <run>
uv run exd --config pilot/runs/<date>/config.yaml review <run>
uv run exd --config pilot/runs/<date>/config.yaml decide <run> <decision> --revision <n> --action SELECT_OPTION --option <id> --by "<researcher>"
uv run exd --config pilot/runs/<date>/config.yaml decide <run> <decision> --revision <n> --action PROPOSE_ALTERNATIVE --text "<actual researcher answer>" --by "<researcher>"
uv run exd --config pilot/runs/<date>/config.yaml resume <run>
```

Responses preserve raw intent and identity with a timestamp. Recording a response
does not call a provider. Resume interprets its authorized scope, applies changes
and requires reconciliation and independent verification. Alternatives need not
match suggested options. DEFER retains a pending scientific decision. The old
`answer` command gives a migration message. `request-changes` is a final-decision
alias that also requires `--revision` and `--by`; optional `--path` narrows its scope.

Final review uses the same decision record. REQUEST_CHANGES, REJECT and HOLD use
`decide` with the presented revision; approval requires the exact current design,
current qualification and a complete scientific handoff.

```text
uv run exd --config <config> decide <run> <final-decision> --revision <n> --action REQUEST_CHANGES --text "<change>" --by "<researcher>"
uv run exd --config <config> approve <run> --version <latest> --by "<researcher>"
uv run exd --config <config> export <run> --out pilot/runs/<date>/final
uv run exd --config <config> export <run> --version 1 --out pilot/runs/<date>/initial
```

Each export has Markdown, authoritative JSON and a checksum manifest from one
historical snapshot. Export a draft for inspection without implying approval.
Publication completes S7 only with current approval; a different or incomplete
existing destination is refused. Preserve all source files, actual decisions and
exports. Use `Store.backup(target)` for a consistent SQLite evidence copy.

`status` displays stage, outstanding decisions, usage and journaled stop scope and
reason. Stable task schemas and transcripts support provider prefix caching;
cache telemetry reports hits separately from missing telemetry. Cached tokens
remain charged to budgets. A committed reviewer submission survives interruption;
it is reused for the same task, while each independent reviewer uses fresh provider
responses. Uncommitted external responses may be retried; this is not exactly-once
provider execution. Review observations stay private until both cohort members have
submitted on the same frozen input.

Resume preserves cumulative run usage and task limits. An explicit operator increase
of the run token ceiling is recorded with old/new limits and a reason:

```text
uv run exd --config <config> resume <run> --llm-token-limit 400000 --reason "<operator reason>"
```

The example AI-feedback source is explicitly a draft. Its SD/ICC/size assumptions
are not district evidence. Confirm the estimand, scientific intent, planning inputs,
SAP and available classes before a scientific acceptance run. Supported t methods
and independent R references are documented in
[statistical methods](../docs/statistical-methods.md); older normal approximations
cannot certify those methods or a mixed-model SAP. Unsupported inputs remain
`not_computed`. Never relabel a source-specified method to obtain a passing number.

Copy the [scorecard](scorecard.md) into each local run directory. Both acceptance
axes require evidence; missing researcher inputs or independent human review leave
Pilot 2 NOT_PROVEN. Generated provider logs and source evidence stay ignored locally.
