"""Collect candidate resources from the researcher, the registry, sources and the Hub."""

import json
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from experiment_design.agents.benchmark.hf import HFClient, HFUnavailable
from experiment_design.agents.benchmark.models import Resource
from experiment_design.agents.structured import structured_call
from experiment_design.config import RunConfig
from experiment_design.contracts import Record
from experiment_design.packs import Pack
from experiment_design.persistence.store import RunStore
from experiment_design.runtime.llm import LLMClient

_SOURCE_CHARS = 4000
_DATASET_SOURCE = re.compile(r"(?:^|/)(?:\d+[-_.]?)?dataset-[^/]*\.json$")


class Mention(Record):
    name: str
    kind: Literal["dataset", "benchmark", "baseline", "metric"]
    source_url: str | None = None
    #: The model's guess of the Hub id ("owner/name"). Only a Hub lookup can verify it.
    hf_repo_id: str | None = None


class SurveyPlan(Record):
    queries: list[str] = Field(default_factory=list, max_length=5)
    tasks: list[str] = Field(default_factory=list)
    mentions: list[Mention] = Field(default_factory=list)


_REPO_ID = re.compile(r"^[\w.-]+(?:/[\w.-]+)?$")


def _repo_id(value: str | None) -> str | None:
    value = (value or "").strip()
    return value if _REPO_ID.match(value) else None


def canonical(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


def resource_id(kind: str, name: str) -> str:
    return f"{kind}:{canonical(name)}"


def _is_dataset_source(path: str) -> bool:
    return bool(_DATASET_SOURCE.search(path.replace("\\", "/")))


def researcher_resources(sources: Mapping[str, str]) -> list[Resource]:
    """Datasets the researcher attached; each source body is a platform.dataset/v1 descriptor."""
    found = []
    for path, text in sources.items():
        if not _is_dataset_source(path):
            continue
        try:
            body = json.loads(text)
            name = body["original_filename"]
        except (ValueError, KeyError, TypeError):
            continue
        if not isinstance(name, str) or not name:
            continue
        version, sha256 = body.get("version_number"), body.get("sha256")
        found.append(
            Resource(
                resource_id=resource_id("dataset", name),
                kind="dataset",
                name=name,
                origin="researcher",
                revision=str(version) if version is not None else None,
                sha256=sha256 if isinstance(sha256, str) and sha256 else None,
            )
        )
    return found


def survey(
    store: RunStore,
    llm: LLMClient,
    cfg: RunConfig,
    pack: Pack,
    hf: HFClient,
    brief_md: str,
    hypothesis_md: str,
    researcher: list[Resource],
) -> list[Resource]:
    sources = {
        path: text[:_SOURCE_CHARS]
        for path, text in store.sources().items()
        if not _is_dataset_source(path)
    }
    plan = structured_call(
        store,
        llm,
        cfg,
        "surveyor",
        {"brief": brief_md, "hypotheses": hypothesis_md, "sources": sources},
        SurveyPlan,
    )
    visible = "\n".join([brief_md, hypothesis_md, *sources.values()])
    tasks = {task.casefold() for task in plan.tasks}
    registry = [
        entry
        for entry in pack.registry
        if not tasks or tasks & {task.casefold() for task in entry.tasks}
    ] or list(pack.registry)
    candidates: list[Resource] = list(researcher)
    candidates += [
        Resource(
            resource_id=resource_id(entry.kind, entry.name),
            kind=entry.kind,
            name=entry.name,
            origin="registry",
            source_url=entry.source_url,
            license=entry.license,
            hf_repo_id=entry.hf_repo_id,
        )
        for entry in registry
    ]
    candidates += [
        Resource(
            resource_id=resource_id(mention.kind, mention.name),
            kind=mention.kind,
            name=mention.name,
            origin="evidence_card",
            # A URL is trusted only when it is quoted in the supplied text.
            source_url=mention.source_url
            if mention.source_url and mention.source_url in visible
            else None,
            hf_repo_id=_repo_id(mention.hf_repo_id),
        )
        for mention in plan.mentions
        if mention.name.strip()
    ]
    for query in plan.queries:
        try:
            hits = hf.search_datasets(query, limit=5)
        except HFUnavailable as exc:
            store.event("benchmark.hub_unavailable", {"query": query, "reason": str(exc)})
            break
        for hit in hits:
            repo_id = hit.get("id")
            if isinstance(repo_id, str) and repo_id:
                candidates.append(
                    Resource(
                        resource_id=resource_id("dataset", repo_id),
                        kind="dataset",
                        name=repo_id,
                        origin="hf_hub",
                        hf_repo_id=repo_id,
                    )
                )
    unique: dict[str, Resource] = {}
    for candidate in candidates:
        unique.setdefault(candidate.resource_id, candidate)
    return list(unique.values())
