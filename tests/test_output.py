import hashlib
import json
import re

import pytest

from experiment_design.contracts import Quantity, ResearcherRequest, ReviewIssue
from experiment_design.output import export, render_markdown
from experiment_design.patch import design_hash
from experiment_design.runtime.store import Store
from tests.support.builders import design, package
from tests.support.history import create_historical_run, save_historical_request


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / "exd.db")
    run = create_historical_run(store, package(), {})
    store.add_version(run, design(), author="design", parent=None)
    store.update_run(run, status="REVIEWABLE")
    yield store, run
    store.close()


def issue(run):
    return ReviewIssue(
        run_id=run,
        raised_by="skeptic",
        version_raised=1,
        severity="MAJOR",
        paths=["/analysis/uncertainty"],
        mechanism="Class correlation may be uncertain",
        consequence="The planned sample may have insufficient power",
        basis="unknown_assumption",
    )


def render(state, status="REVIEWABLE", issues=(), requests=()):
    store, run = state
    return render_markdown(
        package(),
        package().sources,
        store.version(run),
        status,
        1,
        list(issues),
        list(requests),
    )


def test_render_has_no_internal_ids(state):
    store, run = state
    concern = store.save_issue(issue(run))
    request = save_historical_request(
        store,
        ResearcherRequest(
            run_id=run,
            kind="input",
            question="Confirm the class correlation",
            raised_by="design",
        ),
    )
    md = render(state, issues=[concern], requests=[request])
    assert not re.search(r'\bI\d+\b|\bR\d+\b|[0-9a-f]{64}|\{"', md)
    assert "Confirm the class correlation" in md
    assert md.count(package().hypothesis.statement) == 1


def test_render_legacy_power_matches_reference(state):
    section = render(state).split("## 5. Statistical analysis plan")[1].split("## 6.")[0]
    assert "| 0.73 |" in section
    assert "Alpha: 0.025" in section


def test_render_not_computed_scenario(state):
    store, run = state
    d = design()
    d.scenarios[0].sd = None
    store.add_version(run, d, author="design", parent=1)
    md = render(state)
    assert "Not computed" in md and "sd missing" in md


def test_render_designer_default_labelled(state):
    store, run = state
    d = design()
    d.claim.estimand.origin = "designer_default"
    store.add_version(run, d, author="design", parent=1)
    assert "ITT difference in class means (proposed by designer, not approved)" in render(state)


def test_render_blocked_says_no_go(state):
    assert "Not ready: blocking concerns remain (NO-GO)" in render(state, "BLOCKED")


def test_export_binds_version(state, tmp_path):
    store, run = state
    files = export(store, run, tmp_path / "out")
    assert {p.name for p in (tmp_path / "out").iterdir()} == {
        "experiment_design.md",
        "experiment_design.json",
        "manifest.json",
    }
    data = json.loads(files["experiment_design.json"].read_bytes())
    assert data["provenance"]["design_sha256"] == design_hash(store.version(run).design)
    manifest = json.loads(files["manifest.json"].read_bytes())
    for entry in manifest["files"]:
        raw = files[entry["path"]].read_bytes()
        assert entry["bytes"] == len(raw)
        assert entry["sha256"] == hashlib.sha256(raw).hexdigest()
    assert b"\r" not in files["experiment_design.md"].read_bytes()
    refs = re.findall(r'"source_ref": "([^"]+)"', json.dumps(data["design"]))
    ids = {s["id"] for s in data["package"]["sources"] + data["researcher_inputs"]}
    assert set(refs) <= ids


def test_export_old_version_shows_concerns_as_of_that_version(state, tmp_path):
    store, run = state
    concern = store.save_issue(issue(run))
    d = design()
    d.analysis.alpha = 0.05
    store.add_version(run, d, author="design", parent=1)
    concern.status, concern.addressed_in = "resolved", 2
    store.save_issue(concern)
    save_historical_request(
        store,
        ResearcherRequest(
            run_id=run,
            kind="input",
            question="Confirm the revised analysis",
            status="open",
            raised_by="design",
        ),
    )
    old = export(store, run, tmp_path / "old", version=1)
    latest = export(store, run, tmp_path / "latest")
    old_data = json.loads(old["experiment_design.json"].read_bytes())
    latest_data = json.loads(latest["experiment_design.json"].read_bytes())
    assert old_data["design"]["analysis"]["alpha"] == 0.025
    assert latest_data["design"]["analysis"]["alpha"] == 0.05
    assert (old_data["version"], old_data["latest_version"]) == (1, 2)
    assert old_data["pending_requests"] == []
    assert latest_data["pending_requests"] == [
        {"kind": "input", "question": "Confirm the revised analysis", "path": None}
    ]
    assert len(old_data["open_concerns"]) == 1
    assert latest_data["open_concerns"] == []
    assert (
        old["experiment_design.md"]
        .read_text(encoding="utf-8")
        .splitlines()[2]
        .startswith(
            "Earlier version 1",
        )
    )
    assert "Unresolved concern: Class correlation may be uncertain" in old[
        "experiment_design.md"
    ].read_text(encoding="utf-8")


def test_export_keeps_unresolved_after_patch(state, tmp_path):
    store, run = state
    concern = store.save_issue(issue(run))
    store.add_version(run, design(), author="design", parent=1)
    concern.status, concern.addressed_in = "unresolved", 2
    store.save_issue(concern)
    files = export(store, run, tmp_path / "out")
    data = json.loads(files["experiment_design.json"].read_bytes())
    assert data["open_concerns"][0]["mechanism"] == "Class correlation may be uncertain"
    assert "Unresolved concern" in files["experiment_design.md"].read_text(encoding="utf-8")


def test_binary_percent_null_keeps_outcome_scale(state):
    store, run = state
    d = design()
    d.claim.outcome_kind.value = "binary"
    d.claim.outcome_unit.value = "%"
    d.scenarios[0].p_control = Quantity(value=50, unit="%", provenance="assumption")
    d.scenarios[0].p_treatment = Quantity(value=0.5, unit="proportion", provenance="assumption")
    store.add_version(run, d, author="design", parent=1)
    md = render(state)
    assert "Margin: 3 %" in md
    assert "greater than -3 %; the null" in md


def test_small_scientific_threshold_is_not_rounded_to_zero(state):
    store, run = state
    d = design()
    d.claim.margin.value = 0.004
    d.scenarios[0].effect.value = 0.001
    store.add_version(run, d, author="researcher", parent=1)
    md = render(state)
    assert "Margin: 0.004 points" in md
    assert "greater than -0.004 points; the null" in md
    assert "effect: 0.001 points" in md


@pytest.mark.parametrize("status", ["addressed", "rebutted"])
def test_export_keeps_concerns_awaiting_verification(state, tmp_path, status):
    store, run = state
    concern = issue(run)
    concern.severity, concern.status, concern.addressed_in = "BLOCKER", status, 1
    store.save_issue(concern)
    store.update_run(run, status="BUDGET_STOP")
    assert "Awaiting verification: Class correlation may be uncertain" in render(
        state, "BUDGET_STOP", issues=[concern]
    )
    files = export(store, run, tmp_path / "out")
    data = json.loads(files["experiment_design.json"].read_bytes())
    assert data["open_concerns"][0]["severity"] == "BLOCKER"
    assert "Awaiting verification: Class correlation may be uncertain" in files[
        "experiment_design.md"
    ].read_text(encoding="utf-8")
