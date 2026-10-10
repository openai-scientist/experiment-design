# Researcher-reviewed pilot

Before a live run, review `hypothesis.example.md`: confirm the claim, the 3-point margin,
the available classes and the operational constraints. Replace the draft district note
with real data supporting the standard deviation. Confirm or revise the assumed ICC and
class size. The estimand is deliberately absent; answer the resulting request or review
any explicitly labeled default before approval.

The draft calculation uses standard deviation 10 points, ICC 0.10, class size 25, expected
AI–teacher difference 0, and one-sided alpha 0.025. It needs 24 classes per arm for 80%
power; 20 classes per arm give about 73%. One-sided alpha 0.05 gives about 82% power at
20 classes per arm, with a different error threshold. The design must explain this
trade-off and preserve the researcher-owned margin.

Run commands from the repository root. Install with `uv sync`, copy `exd.example.yaml`
to `exd.yaml`, and set the actual tool-calling provider URL and model. Set
`EXD_LLM_API_KEY` and `TYPESAFE_API_KEY` in the environment or the local `.env` file.
The YAML stores environment variable names, not credentials. Use `uv run exd` in place
of `exd` if the virtual environment is not activated. Choose the run date for `<date>`
and substitute the printed ID for `<run>` and request ID for `<request>`.

Preserve the reviewed input and configuration before running (PowerShell `cp` is an
alias for `Copy-Item`; create the directory first):

```text
mkdir pilot/runs/<date>
cp hypothesis.example.md pilot/runs/<date>/hypothesis.md
cp exd.yaml pilot/runs/<date>/config.yaml
exd run pilot/runs/<date>/hypothesis.md
```

Review the output and answer each pending question with actual researcher information:

```text
exd status <run>
exd review <run>
exd answer <run> <request> "<researcher answer>"
exd resume <run>
```

For a requested change or version-bound human approval:

```text
exd request-changes <run> "<required change>" --path /analysis
exd approve <run> --version <latest-version> --by "<researcher name>"
```

Save both versions for comparison. Each directory receives `edp.md`, `design.json` and
`manifest.json` from the selected canonical version:

```text
exd export <run> --out pilot/runs/<date>/final
exd export <run> --version 1 --out pilot/runs/<date>/initial
```

If using a custom configuration, put `--config pilot/runs/<date>/config.yaml` before the
command on every invocation. Keep the SQLite store and journal with the run evidence;
the journal records task results, accepted tool calls, decisions and usage. Resume uses
the last committed action after interruption. Archive the store after the run has stopped.

Copy `pilot/scorecard.md` into the run directory and have a researcher complete it against
both exports and the journal. Record material concerns, independent verification,
improvement or regression, effort and provider costs. Keep the original input snapshot
when later answers clarify the claim. Do not score a run using the draft source as real
district evidence.
