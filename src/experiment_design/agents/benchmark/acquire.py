"""Fetch real Hub metadata for a resource. Nothing is downloaded and nothing is guessed."""

import re
from collections.abc import Callable
from datetime import UTC, datetime

from experiment_design.agents.benchmark.github import GitHubClient, GitHubUnavailable
from experiment_design.agents.benchmark.hf import HFClient, HFUnavailable
from experiment_design.agents.benchmark.models import Resource

_HUB = "https://huggingface.co"
_GITHUB_REPO = re.compile(r"^https://github\.com/([^/\s]+)/([^/\s#?]+)")
_GITHUB_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_GITHUB_RESERVED = {
    "orgs",
    "topics",
    "users",
    "settings",
    "marketplace",
    "features",
    "collections",
    "sponsors",
}


def _github_repo(url: str) -> tuple[str, str] | None:
    match = _GITHUB_REPO.match(url)
    if match is None:
        return None
    owner, name = match[1], match[2].removesuffix(".git")
    if owner.casefold() in _GITHUB_RESERVED:
        return None
    for part in (owner, name):
        if part in {".", ".."} or not _GITHUB_NAME.match(part):
            return None
    return owner, name


def acquire(
    resource: Resource,
    hf: HFClient,
    now: Callable[[], datetime],
    github: GitHubClient | None = None,
) -> Resource:
    repo_id = resource.hf_repo_id or (resource.name if resource.origin == "hf_hub" else None)
    if repo_id is None:
        repo = _github_repo(resource.source_url or "")
        if repo is None or github is None:
            return resource
        return _acquire_github(resource, github, now, *repo)
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


def _acquire_github(
    resource: Resource, github: GitHubClient, now: Callable[[], datetime], owner: str, name: str
) -> Resource:
    try:
        repo = github.repo(owner, name)
        if repo is None:
            return resource.model_copy(
                update={
                    "status": "not_found",
                    "reasons": [*resource.reasons, f"GitHub repository '{owner}/{name}' not found"],
                }
            )
        release = github.latest_release(owner, name)
        tag = release.get("tag_name") if release else None
        ref = tag if isinstance(tag, str) and tag else repo.get("default_branch")
        commit = github.commit(owner, name, ref) if isinstance(ref, str) and ref else None
        revision = commit.get("sha") if commit else None
    except GitHubUnavailable as exc:
        return resource.model_copy(
            update={"reasons": [*resource.reasons, f"GitHub unavailable: {exc}"]}
        )
    license_ = repo.get("license")
    spdx = license_.get("spdx_id") if isinstance(license_, dict) else None
    moment = now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return resource.model_copy(
        update={
            "revision": revision if isinstance(revision, str) and revision else None,
            "license": spdx
            if isinstance(spdx, str) and spdx != "NOASSERTION"
            else resource.license,
            "retrieved_at": moment,
        }
    )
