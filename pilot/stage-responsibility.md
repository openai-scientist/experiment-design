# Stage responsibility evidence

Fill output references and bindings from the actual journal, not from stage labels.
Bindings include source snapshot, context revision, design version/hash and checkpoint.
A controlled test demonstrates a mechanism; mark live evidence not exercised separately.
Pilot 2 evaluates cooperative stages, persistent confirmed decisions, substantive
scientific review and an EDP useful with disclosed assumptions and limitations.
Qualification, exact researcher approval and scientific confirmation are separate
outcomes; unresolved required inputs continue to block execution readiness.

| Stage | Owner | Real input | Output / binding reference | Exit or documented return | Boundary clear? |
|---|---|---|---|---|---|
| S1 Intake | package / workflow | Supplied files | Immutable source snapshot | Readable snapshot or intake error | |
| S2 Understanding | understanding agent / workflow | Exact source and prior context | Context revision; decisions | Provenance recorded or explicit dependency block | |
| S3 Synthesis | designer / patch / Store | Bound context and decisions | Canonical design version/hash | Draft committed; later changes use validated patch | |
| S4 Review and refinement | review / workflow / JEV | Frozen cohort; material issue ledger | Both submissions; patches; verification | Qualification with disclosed limitations, decision or STOP | |
| S5 Qualification | handoff agent / qualification | Current version, accepted calculations and issues | ReadinessAssessment: review status, handoff completeness, execution readiness and blockers | Assessment recorded even when execution is blocked | |
| S6 Researcher review | decisions / qualification | Exact version and presented assessment | Final ResearcherDecision with actual pending/answered state | Pending review, approval, changes, reject or hold | |
| S7 Publication | output / Store | Bound supporting snapshot; approval only for approved output | Draft/approved EDP, stage_artifacts.md/json and manifest | Draft remains REVIEWABLE; approval guards still apply | |

| Cross-cutting contract | Live evidence | Controlled evidence | Missing / limitation |
|---|---|---|---|
| Reviewer isolation until both submissions on one cohort | | `tests/test_review.py` | |
| Alternative intent and authorized, idempotent application | | `tests/test_decisions.py` | |
| Applied reply, explicit unknown-resolution audit, dependent patch and independent scientific verification | Not exercised by controlled tests | `tests/test_stage_pipeline.py::test_researcher_reconciliation_flows_through_scientific_review_to_publication` | Supported class-mean fixture; does not establish mixed-model support or live Pilot 2 completion |
| Resume uses committed work without provider replay | | `tests/test_loop.py`, `tests/test_stage_pipeline.py` | |
| Evidence changes invalidate qualification and approval | | `tests/test_qualification.py`, `tests/test_publication.py` | |
| Historical export retains original supporting revisions | | `tests/test_stages.py`, `tests/test_publication.py` | |
| Mixed-model draft with unverified SD/ICC and unresolved I2 reaches S1–S7 without approval | Not yet live-verified | `tests/test_draft_handoff.py` | Deterministic service fakes establish workflow behavior only; power remains not_computed |

Baseline boundary reconstruction:

Overlap or missing responsibility:

How migrated ownership resolves each ambiguity:

Stage-responsibility verdict and evidence:
