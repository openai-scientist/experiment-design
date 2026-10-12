from experiment_design.contracts import (
    AnalysisPlan,
    Claim,
    ClaimField,
    ClusterSpec,
    Condition,
    Design,
    Hypothesis,
    Inference,
    Measurement,
    Metric,
    Outcome,
    PowerScenario,
    Protocol,
    Quantity,
    Sampling,
    ScientificPackage,
    Source,
)


def package():
    return ScientificPackage(
        id="math",
        research_question="Is AI feedback non-inferior?",
        hypothesis=Hypothesis(
            statement="AI feedback is non-inferior to teacher feedback",
            prediction="Lower bound exceeds -3 points",
            failure_condition="Lower bound is at most -3 points",
            exposure="Weekly AI feedback",
        ),
        scope="Grade-7 classes in one district for one term",
        outcome=Outcome(name="math score", unit="points", kind="continuous", direction="increase"),
        inference=Inference(
            contrast_type="non_inferiority",
            margin=3,
            estimand="ITT difference in class means",
        ),
        constraints=["At most 40 classes"],
        sources=[
            Source(
                id="S1",
                title="District records",
                kind="note",
                text="Standard deviation 10 points. Typical class size 25. ICC 0.10.",
            )
        ],
    )


def design():
    p = package()
    values = {
        "hypothesis": p.hypothesis.statement,
        "prediction": p.hypothesis.prediction,
        "failure_condition": p.hypothesis.failure_condition,
        "exposure": p.hypothesis.exposure,
        "inference_scope": p.scope,
        "primary_outcome": p.outcome.name,
        "outcome_unit": p.outcome.unit,
        "outcome_kind": p.outcome.kind,
        "direction": p.outcome.direction,
        "contrast_type": p.inference.contrast_type,
        "margin": p.inference.margin,
        "estimand": p.inference.estimand,
    }
    return Design(
        claim=Claim(**{k: ClaimField(value=v, origin="package") for k, v in values.items()}),
        conditions=[
            Condition(name="AI", role="treatment", description="Weekly AI written feedback"),
            Condition(name="Teacher", role="control", description="Teacher written feedback"),
        ],
        sampling=Sampling(
            population=p.scope,
            inclusion="Grade 7",
            exclusion="None",
            allocation="1:1",
            randomization="Computer-generated class assignment",
            unit_of_randomization="class",
            unit_of_analysis="student",
            repetitions="One term",
            cluster=ClusterSpec(
                unit="class",
                size=Quantity(value=25, unit="students", provenance="assumption"),
                clusters_per_arm=20,
                icc=Quantity(value=0.10, provenance="source", source_ref="S1"),
            ),
        ),
        measurement=Measurement(
            primary_metric=Metric(
                name="math score",
                kind="continuous",
                unit="points",
                operationalization="Standardized end-of-term score",
                timing="End of term",
            ),
            data_collection="District test records",
            missingness="Report attrition by arm",
        ),
        analysis=AnalysisPlan(
            model="Mixed model with class random intercept",
            sidedness="one_sided",
            alpha=0.025,
            uncertainty="Lower 97.5% one-sided confidence bound",
            decision_rule="Non-inferior if the lower bound exceeds -3 points",
            stopping="Fixed end-of-term analysis; no interim efficacy analysis",
        ),
        scenarios=[
            PowerScenario(
                name="base",
                effect=Quantity(value=0, unit="points", provenance="assumption"),
                sd=Quantity(value=10, unit="points", provenance="source", source_ref="S1"),
            )
        ],
        protocol=Protocol(
            steps=[
                "Assign classes using recorded allocation",
                "Deliver weekly feedback",
                "Collect end-of-term scores",
                "Analyze assigned classes using the specified SAP",
            ],
            stop_conditions=["Suspend collection if research consent is withdrawn"],
        ),
    )
