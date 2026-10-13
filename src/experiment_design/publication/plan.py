"""Publish the current reviewed document without changing its scientific body."""

import hashlib
import json
from pathlib import Path

from experiment_design.contracts import RunState, WorkflowError
from experiment_design.persistence.store import RunStore


def current_plan(store: RunStore) -> str:
    state = store.state
    if state.plan_stage is None or not state.plan_name or not state.plan_hash:
        raise WorkflowError("No plan is available yet; inspect the current question with status.")
    document = store.read(state.plan_stage, state.plan_name)
    if hashlib.sha256(document.encode("utf-8")).hexdigest() != state.plan_hash:
        raise WorkflowError("Current plan identity is stale; resume requires renewed review.")
    return document


def _complete(store: RunStore) -> bool:
    return bool(
        store.state.review_complete
        and store.state.plan_stage is not None
        and store.valid(store.state.plan_stage)
        and store.valid(5)
        and store.valid(6)
    )


def clarifications(store: RunStore) -> list[dict]:
    """Explanations recorded for the current plan, oldest first."""
    return [
        event["data"]
        for event in store.events()
        if event["kind"] == "clarification.answer"
        and event["data"].get("plan_hash") == store.state.plan_hash
    ]


def question_text(state: RunState) -> str:
    """The pending question with its numbered options, for researcher views."""
    options = "".join(f"\n  {n}. {text}" for n, text in enumerate(state.question_options, 1))
    return state.question + options


def _disclosure(store: RunStore, complete: bool) -> dict:
    """Status, approval and gaps shown beside the exact Markdown, never inside it."""
    return {
        "scientific_status": "reviewed_complete" if complete else "incomplete",
        "approval": store.state.approval if complete else "pending",
        "question": store.state.question,
        "review_notes": store.read(5, "final_review.md") if not complete and store.valid(5) else "",
        "gaps": []
        if complete
        else [
            "Fresh complete review is absent or invalid; inspect the current stage reviews.",
            *([store.state.question] if store.state.question else []),
        ],
    }


def render_review(store: RunStore) -> str:
    document = current_plan(store)
    complete = _complete(store)
    disclosure = _disclosure(store, complete)
    quality = "reviewed complete" if complete else "incomplete draft"
    header = f"Scientific review: {quality}. Researcher acceptance: {disclosure['approval']}."
    if disclosure["gaps"]:
        header += "\nKnown gaps:" + "".join(f"\n- {gap}" for gap in disclosure["gaps"])
    if disclosure["review_notes"]:
        header += "\nReview notes:\n" + disclosure["review_notes"]
    if store.state.question:
        header += f"\nResearcher question: {question_text(store.state)}"
    for item in clarifications(store):
        header += f"\nResearcher asked: {item['question']}\nExplanation: {item['markdown']}"
    return header + "\n\n" + document


def _metadata(store: RunStore, *, allow_draft: bool) -> dict:
    current_plan(store)
    complete = _complete(store)
    if not complete and not allow_draft:
        raise WorkflowError("Plan is incomplete; use --allow-draft to export with disclosed gaps.")
    return {
        "run_id": store.state.run_id,
        "attempt": store.state.attempt,
        "plan_hash": store.state.plan_hash,
        **_disclosure(store, complete),
    }


def publish(store: RunStore, *, allow_draft: bool = False) -> Path:
    metadata = _metadata(store, allow_draft=allow_draft)
    document = current_plan(store)
    target = store.write(7, "exp_plan.md", document)
    store.write(7, "publication.json", json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    inputs = [(store.state.plan_stage, store.state.plan_name)]
    if metadata["scientific_status"] == "reviewed_complete":
        inputs.extend([(5, "final_review.md"), (6, "researcher_review.md")])
    store.complete(7, inputs=inputs, outputs=["exp_plan.md", "publication.json"])
    return target


def export(store: RunStore, destination: Path, *, allow_draft: bool = False) -> Path:
    with store.execution():
        return _export(store, destination, allow_draft=allow_draft)


def _export(store: RunStore, destination: Path, *, allow_draft: bool) -> Path:
    metadata = _metadata(store, allow_draft=allow_draft)
    document = current_plan(store)
    destination = Path(destination).resolve()
    run_path = store.path.resolve()
    if (
        destination == run_path
        or destination.is_relative_to(run_path)
        or run_path.is_relative_to(destination)
    ):
        raise WorkflowError("Export destination must not overlap the run directory.")
    if destination.exists():
        raise WorkflowError("Export destination already exists; choose a new directory.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(exist_ok=False)
    # Exclusive creation never replaces an existing export, including concurrent writers.
    with (destination / "exp_plan.md").open("xb") as handle:
        handle.write(document.encode("utf-8"))
    with (destination / "publication.json").open("xb") as handle:
        handle.write((json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return destination / "exp_plan.md"
