import hashlib

import httpx
import pytest

from experiment_design.api.contract_models import SERVICE_KEY_HEADER
from experiment_design.api.inputs import InputError, fetch_inputs
from tests.contract.fake_platform import FakePlatform
from tests.support.api_fakes import make_request


def _http(platform):
    return httpx.Client(
        transport=platform.transport(), headers={SERVICE_KEY_HEADER: platform.service_key}
    )


def test_selection_keeps_only_chosen(tmp_path):
    platform = FakePlatform()
    request = make_request(platform, stage_run_id="sr1", selection=["H1", "H3"])
    paths = fetch_inputs(request, _http(platform), tmp_path)
    text = paths[0].read_text(encoding="utf-8")
    assert "## H1: Statement 1" in text and "## H3: Statement 3" in text and "## H2" not in text
    assert "Budget" not in text and "Constraints" not in text
    assert text.index("## H1") < text.index("## H3")


def test_no_selection_keeps_every_hypothesis_with_its_fields(tmp_path):
    platform = FakePlatform()
    paths = fetch_inputs(make_request(platform, stage_run_id="sr1"), _http(platform), tmp_path)
    text = paths[0].read_text(encoding="utf-8")
    assert text.startswith("# Hypotheses\n")
    assert all(f"## H{n}: Statement {n}" in text for n in (1, 2, 3))
    assert "**falsification_criteria:** Falsified when outcome 1 fails" in text
    assert "  - P1a" in text and "  - P1b" in text
    assert len(paths) == 1 and paths[0].name == "hypotheses.md"


def test_hash_mismatch_rejected(tmp_path):
    platform = FakePlatform()
    request = make_request(platform, stage_run_id="sr1")
    request.inputs.artifacts[0].sha256 = "0" * 64
    with pytest.raises(InputError, match="artifact hash mismatch") as info:
        fetch_inputs(request, _http(platform), tmp_path)
    assert info.value.code == "SHA256_MISMATCH"


def test_unknown_selection_rejected(tmp_path):
    platform = FakePlatform()
    request = make_request(platform, stage_run_id="sr1", selection=["H9"])
    with pytest.raises(InputError, match="unknown hypothesis id"):
        fetch_inputs(request, _http(platform), tmp_path)


def test_exactly_one_hypotheses_artifact_required(tmp_path):
    platform = FakePlatform()
    request = make_request(platform, stage_run_id="sr1")
    request.inputs.artifacts.clear()
    with pytest.raises(InputError, match="expected one i2h.hypotheses/v1 input"):
        fetch_inputs(request, _http(platform), tmp_path)


def test_download_requires_the_service_key(tmp_path):
    platform = FakePlatform()
    request = make_request(platform, stage_run_id="sr1")
    anonymous = httpx.Client(transport=platform.transport())
    with pytest.raises(InputError, match="input download failed"):
        fetch_inputs(request, anonymous, tmp_path)


@pytest.mark.parametrize(
    "body", [b"not json", b'{"hypotheses": []}', b'{"hypotheses": [{"id": "H1"}]}']
)
def test_malformed_hypotheses_rejected(tmp_path, body):
    platform = FakePlatform()
    request = make_request(platform, stage_run_id="sr1")
    platform.hosted[request.inputs.artifacts[0].artifact_id] = body
    request.inputs.artifacts[0].sha256 = hashlib.sha256(body).hexdigest()
    with pytest.raises(InputError, match="malformed"):
        fetch_inputs(request, _http(platform), tmp_path)
