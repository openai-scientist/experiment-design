import pytest

from experiment_design.contracts import WorkflowError
from tests.unit.benchmark.test_survey_select import _store


def test_activity_records_payload_and_rejects_unknown_type(tmp_path):
    store = _store(tmp_path)
    store.activity("exd.resources.surveyed", actor="surveyor", summary="s", payload={"count": 2})
    entry = store.events()[-1]
    assert entry["kind"] == "activity"
    assert entry["data"]["payload"] == {"count": 2}
    with pytest.raises(WorkflowError):
        store.activity("exd.unknown", actor="x", summary="s")
