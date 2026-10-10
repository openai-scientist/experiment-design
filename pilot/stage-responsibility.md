# Stage responsibility evidence

Fill output references and bindings from the actual journal, not from stage labels.
Bindings include source snapshot, context revision, design version/hash and checkpoint.
A controlled test demonstrates a mechanism; mark live evidence not exercised separately.

| Stage | Owner | Real input | Output / binding reference | Exit or documented return | Boundary clear? |
|---|---|---|---|---|---|
| S1 Intake | package / workflow | Supplied files | Immutable source snapshot | Readable snapshot or intake error | |
| S2 Understanding | understanding agent / workflow | Exact source and prior context | Context revision; decisions | Provenance recorded or explicit dependency block | |
| S3 Synthesis | designer / patch / Store | Bound context and decisions | Canonical design version/hash | Draft committed; later changes use validated patch | |
| S4 Review and refinement | review / workflow / JEV | Frozen cohort; material issue ledger | Both submissions; patches; verification | Material closure, decision, stop or authorized return | |
| S5 Qualification | handoff agent / qualification | Current version, accepted calculations and issues | ReadinessAssessment | Complete handoff, blockers or documented return | |
| S6 Researcher review | decisions / qualification | Exact version and presented assessment | Final decision; explicit approval projection | Approve, changes, reject or hold | |
| S7 Publication | output / Store | Frozen supporting snapshot and approval | Atomic bundle and manifest | Complete approved publication or visible failure | |

| Cross-cutting contract | Live evidence | Controlled evidence | Missing / limitation |
|---|---|---|---|
| Reviewer isolation until both submissions on one cohort | | `tests/test_review.py` | |
| Alternative intent and authorized, idempotent application | | `tests/test_decisions.py` | |
| Resume uses committed work without provider replay | | `tests/test_loop.py`, `tests/test_stage_pipeline.py` | |
| Evidence changes invalidate qualification and approval | | `tests/test_qualification.py`, `tests/test_publication.py` | |
| Historical export retains original supporting revisions | | `tests/test_stages.py`, `tests/test_publication.py` | |

Baseline boundary reconstruction:

Overlap or missing responsibility:

How migrated ownership resolves each ambiguity:

Stage-responsibility verdict and evidence:
