import hashlib
import json
import logging

import httpx
import pytest

from experiment_design.api.contract_models import SERVICE_KEY_HEADER, Decisions
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


def _plain(tmp_path):
    platform = FakePlatform()
    request = make_request(platform, stage_run_id="sr1")
    return platform, request, fetch_inputs(request, _http(platform), tmp_path / "plain")[0]


def test_brief_lists_note_and_guidance_after_hypotheses(tmp_path):
    platform, request, plain = _plain(tmp_path)
    request.inputs.decisions = Decisions(actor="system", note="Focus on H1")
    request.inputs.params["guidance"] = ["Use a paired design", "Keep n small"]
    path = fetch_inputs(request, _http(platform), tmp_path / "brief")[0]
    text = path.read_text(encoding="utf-8")
    base = plain.read_text(encoding="utf-8")
    assert text.startswith(base)
    brief = text[len(base) :]
    assert "## Research lead brief" in brief
    assert "- Focus on H1" in brief and "- Use a paired design" in brief
    assert "- Keep n small" in brief


def test_guidance_alone_adds_the_brief(tmp_path):
    platform, request, _ = _plain(tmp_path)
    request.inputs.params["guidance"] = ["Only this"]
    text = fetch_inputs(request, _http(platform), tmp_path / "g")[0].read_text(encoding="utf-8")
    assert text.endswith("## Research lead brief\n\n- Only this\n")


def test_no_note_or_guidance_leaves_the_file_unchanged(tmp_path):
    platform, request, plain = _plain(tmp_path)
    request.inputs.decisions = Decisions(actor="user", note=None)
    request.inputs.params["guidance"] = []
    path = fetch_inputs(request, _http(platform), tmp_path / "same")[0]
    assert path.read_bytes() == plain.read_bytes()
    assert "Research lead brief" not in plain.read_text(encoding="utf-8")


@pytest.mark.parametrize("guidance", ["a string", [1, 2], ["ok", 3], {"a": "b"}])
def test_malformed_guidance_rejected(tmp_path, guidance):
    platform, request, _ = _plain(tmp_path)
    request.inputs.params["guidance"] = guidance
    with pytest.raises(InputError, match="guidance"):
        fetch_inputs(request, _http(platform), tmp_path / "bad")


def _hypotheses():
    return [
        {"id": "H1", "statement": "S1", "evidence_refs": ["card-p-1", "card-p-9"]},
        {"id": "H2", "statement": "S2"},
    ]


def _add(platform, request, schema, body, *, raw=None):
    content = raw if raw is not None else json.dumps(body).encode()
    request.inputs.artifacts.append(platform.add_artifact(content, schema_id=schema))


def _evidence():
    long = "x" * 600
    return {
        "schema_version": "i2h.evidence/v1",
        "cards": [
            {
                "card_id": "card-p-1",
                "paper_id": "p-1",
                "title": "Repair study",
                "year": 2024,
                "doi": "10.1/abc",
                "data": "Defects4J",
                "metrics": "pass@1",
                "findings": long,
                "limitations": "",
                "hypothesis_ids": ["H1"],
            }
        ],
    }


def _review():
    def finding(fid, severity, locator):
        return {
            "finding_id": fid,
            "anchor": {"locator": locator},
            "severity": severity,
            "issue": f"issue {fid}",
            "author": "reviewer",
        }

    return {
        "findings": [
            finding("F1-1", "blocking", "hypothesis:H1.criteria"),
            finding("F1-2", "major", "document:hypotheses"),
            finding("F1-3", "minor", "hypothesis:H1"),
            finding("F1-4", "blocking", "hypothesis:H2"),
        ]
    }


def test_evidence_is_rendered_under_the_citing_hypothesis(tmp_path):
    platform = FakePlatform()
    request = make_request(platform, stage_run_id="sr1", hypotheses=_hypotheses())
    _add(platform, request, "i2h.evidence/v1", _evidence())
    text = fetch_inputs(request, _http(platform), tmp_path)[0].read_text(encoding="utf-8")
    h1, h2 = text.index("## H1"), text.index("## H2")
    assert h1 < text.index("### Evidence") < h2
    assert "- Repair study (2024) — 10.1/abc" in text
    assert "  - Data: Defects4J" in text and "  - Metrics: pass@1" in text
    assert "x" * 600 not in text and text.count("### Evidence") == 1


def test_known_issues_list_blocking_and_major_for_selected_hypotheses(tmp_path):
    platform = FakePlatform()
    request = make_request(platform, stage_run_id="sr1", hypotheses=_hypotheses(), selection=["H1"])
    _add(platform, request, "review.findings/v1", _review())
    request.inputs.params["guidance"] = ["Be brief"]
    text = fetch_inputs(request, _http(platform), tmp_path)[0].read_text(encoding="utf-8")
    issues = text[text.index("## Known issues from hypothesis review") :]
    assert "[F1-1] (blocking) hypothesis:H1.criteria: issue F1-1" in issues
    assert "[F1-2] (major) document:hypotheses: issue F1-2" in issues
    assert "F1-3" not in text and "F1-4" not in text
    assert text.index("## Known issues") < text.index("## Research lead brief")


def test_malformed_or_tampered_optional_inputs_are_skipped_with_a_warning(tmp_path, caplog):
    platform = FakePlatform()
    plain = fetch_inputs(
        make_request(platform, stage_run_id="sr0", hypotheses=_hypotheses()),
        _http(platform),
        tmp_path / "plain",
    )[0]
    request = make_request(platform, stage_run_id="sr1", hypotheses=_hypotheses())
    _add(platform, request, "i2h.evidence/v1", {"cards": [{"title": "no id"}]})
    _add(platform, request, "review.findings/v1", None, raw=b"not json")
    tampered = platform.add_artifact(b"{}", schema_id="i2h.evidence/v1")
    platform.hosted[tampered.artifact_id] = b'{"cards": []}'
    request.inputs.artifacts.append(tampered)
    with caplog.at_level(logging.WARNING):
        path = fetch_inputs(request, _http(platform), tmp_path / "bad")[0]
    assert path.read_bytes() == plain.read_bytes()
    assert (
        len([r for r in caplog.records if "optional" in r.message or "malformed" in r.message]) == 3
    )


def test_hostile_cards_and_foreign_findings_are_neutralised(tmp_path):
    platform = FakePlatform()
    hypotheses = _hypotheses()
    hypotheses[0]["evidence_refs"] = ["card-p-1", 7, None]
    request = make_request(platform, stage_run_id="sr1", hypotheses=hypotheses, selection=["H1"])
    evidence = _evidence()
    evidence["cards"][0] |= {"year": "2024\n## Injected", "doi": "10.1/a\n# Heading"}
    _add(platform, request, "i2h.evidence/v1", evidence)
    review = _review()
    review["findings"] += [
        {
            "finding_id": "F9",
            "anchor": {"locator": "document:x", "artifact_id": "other"},
            "severity": "major",
            "issue": "foreign",
            "author": "reviewer",
        },
        {
            "finding_id": "F8",
            "anchor": {"locator": "hypothesis:H1.2", "artifact_id": None},
            "severity": "major",
            "issue": "dotted",
            "author": "reviewer",
        },
    ]
    _add(platform, request, "review.findings/v1", review)
    text = fetch_inputs(request, _http(platform), tmp_path)[0].read_text(encoding="utf-8")
    assert "\n## Injected" not in text and "\n# Heading" not in text
    assert "(2024 ## Injected)" in text
    assert "F9" not in text
    assert "[F8] (major) hypothesis:H1.2: dotted" in text
