# Experiment Plan: LLM-based repair versus a template-based tool on Defects4J

Illustration only. The agents have not run, there are no results and the researcher has not accepted this plan. It follows `hypotheses.example.md`.

## 1. Question and decision rule

Does the LLM-based repair tool fix more Defects4J bugs than the template-based baseline (H1), and are its plausible fixes at least as often correct (H2)?

H1 is concluded only if the 95% confidence interval of the paired difference in plausible fix rate is above zero. Otherwise the plan reports no demonstrated advantage; it does not report that the tool is worse.

## 2. Experiment E1: comparative benchmark

- Benchmark: Defects4J. Release, bug count and license: needs verification against the project page before launch.
- Systems: the LLM-based tool and the template-based tool, each at a fixed version.
- Controls: same bug set, same patch budget per bug, same time limit and the same test harness for both systems.
- Test flakiness: rerun failing and passing validation at least three times and exclude bugs with unstable test outcomes, reporting the exclusions.
- Contamination: Defects4J is public and may be in the LLM training data. Report this as a limitation and, when feasible, add a second benchmark that postdates the model training cutoff.
- Project diversity: report results per project and do not let one project dominate the aggregate.

## 3. Experiment E2: patch correctness review

Two blinded reviewers label a random sample of plausible patches against the developer fix; a third reviewer resolves disagreement. Report agreement.

## 4. Prespecified analysis

McNemar test on per-bug success for E1; difference in correctness rate with a confidence interval for E2. Correct for multiple comparisons across the two hypotheses with the Holm procedure.

## 5. Limitations

Java only, one release of one benchmark, test-suite passing is not proof of correctness, and manual review sample size limits E2.
