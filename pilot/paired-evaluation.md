# Paired evaluation

Freeze the comparison before paying for provider calls. Preserve baseline
`ef4f561` in an isolated checkout, and record the migrated commit separately.
Use one researcher-reviewed AI-feedback source, preserving the same scientific
intent, constraints, estimand, supported evidence and planning assumptions in both
arms. Baseline may require a heading template; keep an explicit field/source
mapping to the migrated freeform files. Record every difference and its effect
on comparability. Do not invent missing SD, ICC, estimand or feasibility inputs.

Freeze provider/model IDs, configurations, comparable budgets and the four-dimension
[scorecard](scorecard.md). Use fresh independent provider responses in each arm.
Source-specified SAP changes require an actual researcher decision and consistent
inputs in both arms, or an explicit comparison limitation. Record actual calls,
tokens, cache telemetry, latency, JEV overhead and billing costs where available;
unknown costs remain unknown. Low token counts do not establish scientific quality.

Before live spending, run the complete local checks and inspect mutation evidence.
Save the original sources and typed configuration without secrets, commits and
execution dates. Keep initial/final bundles, append-only journal, observations,
raw researcher replies, applied changes, verification and readiness. Back up the
migrated store through `Store.backup`; stop baseline writes before its consistent
SQLite backup. Preserve partial failed runs without declaring them matched successes.

Present material researcher decisions after useful exploration or at a dependency
block. Do not author replies or approval under a researcher's identity. An alternative,
return or interruption that the live case does not exercise is marked **not exercised**;
controlled behavioral tests provide separate mechanism evidence.

A capable independent researcher reviews both EDPs against the same rubric and
source/SAP/power/protocol evidence. Hide arm labels during initial review where
feasible. Identify downstream scientific choices still required, unresolved material
blockers and regressions. Any drop in a rubric dimension needs a reported evidence-based
investigation; it is not averaged away. Baseline weakness does not lower the acceptance
bar. A same-model automated score is advisory and cannot substitute for researcher review.

Complete the [responsibility worksheet](stage-responsibility.md) from actual artifacts
and journal events, distinguishing live evidence from controlled tests. Compare which
baseline boundaries require reconstruction and which migrated boundaries have explicit
owners, bindings and exits. Record implementation clarity separately from EDP quality.

The report ends with these independent verdicts:

```text
stage-responsibility: PASS|FAIL|NOT_PROVEN
EDP-quality: PASS|FAIL|NOT_PROVEN
overall: PASS|FAIL|NOT_PROVEN
```

Overall PASS requires both axes PASS, no unresolved material blocker, supported
numbers and an implementation-ready handoff. Missing source approval, baseline or human
review means NOT_PROVEN. A concrete failure is useful diagnostic evidence. Results support
this paired case, not universal superiority of seven stages.
