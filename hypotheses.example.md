# Hypotheses

**Topic:** Weekly AI-generated feedback on grade-7 math homework compared with teacher-written feedback

## H1: AI-generated homework feedback is non-inferior to teacher-written feedback in end-of-term math scores.

- **Exposure:** Weekly AI-generated written feedback on math homework, with teacher-written feedback as the control.
- **Outcome:** End-of-term standardized math score (points, 0–100).
- **Estimand:** Difference in mean score, AI minus teacher.
- **Method:** Class-randomized trial; mixed model with class random effect.
- **Prediction:** The lower 97.5% one-sided confidence bound of (AI − teacher) is above −3 points.
- **Falsified if:** The lower bound is ≤ −3 points.
- **Rationale:** Timely, specific feedback may match teacher feedback in learning effect.
- **Limitations:**
  - Teachers cannot be blinded.

## H2: AI-generated feedback reduces teacher time spent on homework feedback.

- **Exposure:** Same as H1.
- **Outcome:** Teacher minutes spent producing homework feedback per week (self-logged).
- **Estimand:** Difference in mean weekly feedback minutes per teacher, AI minus teacher.
- **Method:** Same trial as H1; teacher-level time logs.
- **Prediction:** Mean weekly feedback time is at least 30% lower in the AI arm.
- **Falsified if:** The reduction is below 30% or the 95% confidence interval includes zero.
- **Rationale:** Teachers only review AI drafts instead of writing feedback.
- **Limitations:**
  - Self-logged time may be biased.

## H3: AI-generated feedback widens the score gap for students with low baseline achievement.

- **Exposure:** Same as H1.
- **Outcome:** End-of-term standardized math score (points, 0–100).
- **Estimand:** Interaction of arm and low baseline achievement (bottom tertile of prior-year score) on end-of-term score.
- **Method:** Same trial as H1; arm-by-baseline interaction in the mixed model.
- **Prediction:** Among bottom-tertile students, AI minus teacher is at least 2 points lower than among other students.
- **Falsified if:** The interaction estimate is above −2 points or its 95% confidence interval includes zero.
- **Rationale:** Struggling students may need explanations that generic AI feedback does not give (contrarian view).
- **Limitations:**
  - Subgroup power is limited with 40 classes.

## Scope
Grade-7 classes in one district, one term; scores range from 0 to 100.

## Constraints
- at most 40 classes available (20 per arm)
- teachers cannot be blinded
- one term

## Budget
District analyst time ≈ 40 h.

## Assumptions
- A1: Class size about 25 students {value: 25, unit: students}
- A2: ICC 0.10 for class-level clustering, to be confirmed from district records {value: 0.10, unit: ""}

## Sources
### S1: District note (DRAFT)
DRAFT — researcher must replace with real district data before the pilot is scored.

The standard deviation of last year's term scores was about 10 points. The typical class size was about 25 students.
