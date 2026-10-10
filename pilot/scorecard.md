# Researcher pilot scorecard

Complete after reviewing the input snapshot and both exported designs. Leave scores blank
until a human researcher has assessed the evidence. A same-model judge is reference only;
its opinion is not independent ground truth.

Run ID:
Reviewer:
Review date:
Input snapshot:
Configuration snapshot (environment variable names only):
Baseline commit/run/export:
Migrated commit/run/initial/final export:
Source field mapping:
Provider/model IDs and budget comparability:

| Dimension | Score (1–5) | Evidence | Reviewer comment |
| --- | --- | --- | --- |
| Scientific alignment: preserves hypothesis, outcome, null and estimand | | | |
| Methodological soundness: units, controls, sampling, estimation, uncertainty and decision rules | | | |
| Executability: another researcher or engineer can implement the design | | | |
| Clarity & evidence honesty: readable evidence, scenarios, assumptions and limitations | | | |

Blocking gaps:

Improvement vs initial:

Regressions:

Material concerns, patches or rebuttals, and independent verification outcomes:

Evidence that agents adapted to tool observations (task/tool/evidence records):

Researcher effort (minutes):

Tokens / calls / latency (include LLM and JEV separately where available):

Verdict and failure modes:

An unsuccessful pilot is useful when it identifies a concrete failure mode. Do not force
approval or fill missing scientific inputs to make the run look successful.

Independent handoff assessment (source, assumptions, SAP, power, protocol and remaining scientific choices):

Per-dimension baseline comparison (investigate every drop):

Comparison limitations and mechanisms not exercised live:

Responsibility worksheet:

stage-responsibility: NOT_PROVEN
EDP-quality: NOT_PROVEN
overall: NOT_PROVEN

Change a verdict only when its evidence is recorded. Overall PASS requires both axes
PASS; scores are not an automatic READY threshold. An unfilled human scorecard remains
NOT_PROVEN. Baseline weaknesses never reduce the scientific acceptance bar.
