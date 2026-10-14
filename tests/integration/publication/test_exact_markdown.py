import hashlib
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from experiment_design.publication import export, publish, render_review


class DocumentStore:
    def __init__(self, path, *, complete=True):
        self.path = path
        self.document = "# Study\r\n\r\nTiếng Việt — exact.\r\n"
        self.state = SimpleNamespace(
            run_id="exd-test",
            attempt=1,
            plan_stage=4,
            plan_name="revised_plan.md",
            plan_hash=hashlib.sha256(self.document.encode()).hexdigest(),
            review_complete=complete,
            approval="pending",
            status="AWAITING_RESEARCHER",
            question="",
            question_kind="",
            profile=None,
        )
        self.valid_stages = {4, 5, 6}
        self.recorded = []

    def read(self, stage, name):
        return self.document

    def valid(self, stage):
        return stage in self.valid_stages

    def write(self, stage, name, text):
        target = self.path / f"stage-{stage:02d}" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
        return target

    def complete(self, stage, inputs, outputs):
        self.valid_stages.add(stage)

    def execution(self):
        return nullcontext(self)

    def events(self):
        return self.recorded

    def event(self, kind, data):
        self.recorded.append({"kind": kind, "data": data, "timestamp": "t"})


def test_pending_acceptance_publishes_exact_bytes(tmp_path):
    store = DocumentStore(tmp_path / "run")
    result = publish(store)
    assert result.read_bytes() == store.document.encode()
    assert "pending" in render_review(store)
    assert store.state.approval == "pending"


def test_incomplete_requires_explicit_draft_and_exposes_gaps(tmp_path):
    store = DocumentStore(tmp_path / "run", complete=False)
    with pytest.raises(ValueError, match="incomplete"):
        publish(store)
    result = publish(store, allow_draft=True)
    assert result.read_bytes() == store.document.encode()
    assert "incomplete" in (result.parent / "publication.json").read_text()


@pytest.mark.parametrize("change", ["plan", "final_review"])
def test_stale_document_or_review_cannot_publish_complete(tmp_path, change):
    store = DocumentStore(tmp_path / "run")
    if change == "plan":
        store.document += "changed"
    else:
        store.valid_stages.remove(5)
    with pytest.raises(ValueError):
        publish(store)


def test_export_refuses_existing_destination_and_run_overlap(tmp_path):
    store = DocumentStore(tmp_path / "run")
    destination = tmp_path / "export"
    export(store, destination)
    assert (destination / "exp_plan.md").read_bytes() == store.document.encode()
    with pytest.raises(ValueError, match="exist"):
        export(store, destination)
    with pytest.raises(ValueError, match="overlap"):
        export(store, store.path / "stage-01")


def test_invalidated_review_does_not_export_old_approval(tmp_path):
    store = DocumentStore(tmp_path / "run", complete=False)
    store.state.approval = "approved"
    target = publish(store, allow_draft=True)
    import json

    assert json.loads((target.parent / "publication.json").read_text())["approval"] == "pending"
    assert "acceptance: pending" in render_review(store)


def test_review_shows_current_plan_explanations_outside_exact_body(tmp_path):
    store = DocumentStore(tmp_path / "run")
    current = store.state.plan_hash
    store.event(
        "clarification.answer",
        {"question": "Why A?", "markdown": "Because A.", "plan_hash": current},
    )
    store.event(
        "clarification.answer",
        {"question": "Old?", "markdown": "Stale text.", "plan_hash": "other"},
    )
    store.event(
        "clarification.answer",
        {"question": "Why B?", "markdown": "Because B.", "plan_hash": current},
    )
    text = render_review(store)
    assert text.endswith("\n\n" + store.document)
    header = text[: -len(store.document)]
    assert header.index("Why A?") < header.index("Because A.") < header.index("Why B?")
    assert "Because B." in header
    assert "Old?" not in text and "Stale text." not in text
