import pytest
from conftest import design, package

from experiment_design.contracts import ResearcherRequest, ReviewIssue


@pytest.mark.parametrize(
    "record",
    [
        package(),
        design(),
        ReviewIssue(
            run_id="r1",
            raised_by="skeptic",
            version_raised=1,
            severity="MAJOR",
            paths=["/analysis/model"],
            mechanism="Model omits class effects",
            consequence="Uncertainty is underestimated",
            basis="reasoning",
        ),
        ResearcherRequest(
            run_id="r1",
            kind="decision",
            question="Which estimand?",
            path="/claim/estimand",
            raised_by="design",
        ),
    ],
)
def test_records_round_trip_through_json(record):
    assert type(record).model_validate_json(record.model_dump_json()) == record
