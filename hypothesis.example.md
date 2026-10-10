# Is weekly AI-generated feedback on grade-7 math homework non-inferior to teacher-written feedback in end-of-term math scores?

## Hypothesis
AI-generated homework feedback is non-inferior to teacher-written feedback.
- prediction: The lower 97.5% one-sided confidence bound of (AI − teacher) is above −3 points.
- failure condition: The lower bound is ≤ −3 points.
- exposure: Weekly AI-generated written feedback on math homework, with teacher-written feedback as the control.
- method: Class-randomized trial; mixed model with class random effect.

## Outcome
- name: end-of-term standardized math score
- unit: points
- kind: continuous
- direction: increase

## Inference
- contrast: non_inferiority
- margin: 3

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
