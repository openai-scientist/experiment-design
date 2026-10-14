"""Fetch real Hub metadata for a resource. Nothing is downloaded and nothing is guessed."""

from collections.abc import Callable
from datetime import UTC, datetime

from experiment_design.agents.benchmark.hf import HFClient, HFUnavailable
from experiment_design.agents.benchmark.models import Resource

_HUB = "https://huggingface.co"


def acquire(resource: Resource, hf: HFClient, now: Callable[[], datetime]) -> Resource:
    repo_id = resource.hf_repo_id or (resource.name if resource.origin == "hf_hub" else None)
    if repo_id is None:
        return resource
    is_model = resource.kind == "baseline"
    try:
        info = hf.model_info(repo_id) if is_model else hf.dataset_info(repo_id)
    except HFUnavailable as exc:
        return resource.model_copy(
            update={"reasons": [*resource.reasons, f"Hub unavailable: {exc}"]}
        )
    if info is None:
        return resource.model_copy(
            update={
                "status": "not_found",
                "reasons": [*resource.reasons, f"Hub repository '{repo_id}' not found"],
            }
        )
    card = info.get("cardData")
    card = card if isinstance(card, dict) else {}
    license_ = card.get("license")
    revision = info.get("sha")
    size = info.get("usedStorage")
    splits = _split_names(card)
    path = f"/{repo_id}" if is_model else f"/datasets/{repo_id}"
    moment = now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return resource.model_copy(
        update={
            "revision": revision if isinstance(revision, str) and revision else None,
            "license": license_ if isinstance(license_, str) else resource.license,
            "splits": splits or resource.splits,
            "size_bytes": size if isinstance(size, int) and not isinstance(size, bool) else None,
            "retrieved_at": moment,
            "source_url": f"{_HUB}{path}",
        }
    )


def _split_names(card: dict) -> list[str]:
    declared = card.get("dataset_info")
    entries = declared if isinstance(declared, list) else [declared]
    names: dict[str, None] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for split in entry.get("splits") or []:
            if isinstance(split, dict) and isinstance(split.get("name"), str):
                names[split["name"]] = None
    return list(names)
