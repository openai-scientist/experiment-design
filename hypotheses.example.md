# Hypotheses

**Topic:** LLM-based automated program repair compared with a template-based baseline on Defects4J

## H1: The LLM-based repair tool fixes more Defects4J bugs than the template-based baseline.

- **Exposure:** Candidate patch generation by the LLM-based repair tool, with the template-based repair tool as the control.
- **Outcome:** Fraction of bugs for which at least one generated patch passes the full developer test suite (plausible fix rate).
- **Estimand:** Difference in plausible fix rate on the same bugs, LLM tool minus baseline.
- **Method:** Paired comparison on the same bug set, same patch budget per bug and same time limit; McNemar test on per-bug success.
- **Prediction:** The LLM tool fixes a larger share of bugs; the paired difference is positive with a 95% confidence interval above zero.
- **Falsified if:** The paired difference is zero or negative, or its 95% confidence interval includes zero.
- **Rationale:** Large language models may propose edits outside a fixed template set.
- **Limitations:**
  - Defects4J bugs may appear in LLM pre-training data (benchmark contamination).
  - Passing the test suite does not prove the patch is correct.

## H2: Plausible fixes of the LLM-based tool are not less often semantically correct than those of the baseline.

- **Exposure:** Same as H1.
- **Outcome:** Fraction of plausible patches judged equivalent to the developer fix by blinded manual review.
- **Estimand:** Difference in correctness rate among plausible patches, LLM tool minus baseline.
- **Method:** Two blinded reviewers label a random sample of plausible patches from each tool; disagreement resolved by a third reviewer.
- **Prediction:** The lower confidence bound of the difference is above a non-inferiority margin the researcher must state.
- **Falsified if:** The lower bound is at or below the margin.
- **Rationale:** More plausible fixes are only useful if they are also correct.
- **Limitations:**
  - Manual review is costly; the reviewed sample may be small.

## Scope
Java bugs in Defects4J, one tool version per system, a fixed patch budget per bug.

## Sources
### S1: Researcher note (DRAFT)
DRAFT — the researcher must replace this with the tool versions, the Defects4J release and any pilot results before the plan is scored.
