"""Fetch real Hub metadata for a resource. Nothing is downloaded and nothing is guessed."""

import re
from collections.abc import Callable
from datetime import UTC, datetime

from experiment_design.agents.benchmark.github import GitHubClient, GitHubUnavailable
from experiment_design.agents.benchmark.hf import HFClient, HFUnavailable
from experiment_design.agents.benchmark.models import Resource
from experiment_design.agents.benchmark.zenodo import ZenodoClient, ZenodoUnavailable

_HUB = "https://huggingface.co"
_GITHUB_REPO = re.compile(r"^https://github\.com/([^/\s]+)/([^/\s#?]+)")
_ZENODO_RECORD = re.compile(r"10\.5281/zenodo\.(\d+)|zenodo\.org/records?/(\d+)", re.IGNORECASE)
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
    zenodo: ZenodoClient | None = None,
) -> Resource:
    record = _ZENODO_RECORD.search(resource.source_url or "")
    if record is not None:
        if zenodo is None:
            return resource
        return _acquire_zenodo(resource, zenodo, now, record[1] or record[2])
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
    if isinstance(license_, list):  # the Hub allows a list of licenses
        license_ = ", ".join(x for x in license_ if isinstance(x, str)) or None
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


def _acquire_zenodo(
    resource: Resource, zenodo: ZenodoClient, now: Callable[[], datetime], record_id: str
) -> Resource:
    try:
        record = zenodo.record(record_id)
    except ZenodoUnavailable as exc:
        return resource.model_copy(
            update={"reasons": [*resource.reasons, f"Zenodo unavailable: {exc}"]}
        )
    if record is None:
        return resource.model_copy(
            update={
                "status": "not_found",
                "reasons": [*resource.reasons, f"Zenodo record '{record_id}' not found"],
            }
        )
    metadata = record.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    doi, version = record.get("doi"), metadata.get("version")
    # A concept DOI redirects to its latest version: pin the record actually returned.
    returned = record.get("id")
    if isinstance(returned, int | str) and not isinstance(returned, bool) and str(returned):
        record_id = str(returned)
    revision = f"zenodo:{record_id}"
    details = [f"{label} {value}" for label, value in (("doi", doi), ("version", version)) if value]
    if details:
        revision += " (" + ", ".join(details) + ")"
    license_ = metadata.get("license")
    license_id = license_.get("id") if isinstance(license_, dict) else None
    checksums = [
        f"{f['key']} {f['checksum']}"
        for f in record.get("files") or []
        if isinstance(f, dict) and isinstance(f.get("key"), str) and f.get("checksum")
    ]
    # Resource has no checksum field for a record's files; the notes carry them.
    notes = [f"Zenodo file checksums: {'; '.join(checksums)}"] if checksums else []
    return resource.model_copy(
        update={
            "revision": revision,
            "license": license_id
            if isinstance(license_id, str) and license_id
            else resource.license,
            "retrieved_at": now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_url": f"https://zenodo.org/records/{record_id}",
            "reasons": [*resource.reasons, *notes],
        }
    )
