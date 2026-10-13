"""Read researcher-written hypothesis Markdown without filling scientific gaps."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from experiment_design.contracts import (
    ResearchContext,
    ResearcherDecision,
    ResearcherRequest,
    ScientificPackage,
    Source,
    SourceArtifact,
    SourceSnapshot,
)


class PackageFormatError(ValueError):
    pass


def snapshot_sources(paths: list[Path]) -> SourceSnapshot:
    if not paths:
        raise PackageFormatError("at least one readable Markdown input is required")
    artifacts, issues, visited = [], [], set()

    def read(path: Path, required: bool) -> None:
        path = path.resolve()
        if path in visited and (not required or any(a.path == str(path) for a in artifacts)):
            return
        visited.add(path)
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            if required:
                raise PackageFormatError(f"cannot read input {path}: {exc}") from exc
            issues.append(f"Cannot read referenced source {path}: {type(exc).__name__}")
            return
        digest = hashlib.sha256(raw).hexdigest()
        ref = "source:" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
        artifacts.append(
            SourceArtifact(
                ref=ref,
                path=str(path),
                text=text,
                sha256=digest,
                metadata={"role": "input" if required else "attachment"},
            )
        )
        for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text):
            parsed = urlparse(target.strip().strip("<>"))
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            linked = path.parent / unquote(parsed.path)
            if linked.suffix.lower() in {".md", ".txt", ".csv", ".json"}:
                read(linked, False)
            elif not linked.exists():
                issues.append(f"Missing referenced source {linked}")

    for path in paths:
        read(Path(path), True)
    identity = json.dumps([a.model_dump(mode="json") for a in artifacts], sort_keys=True)
    return SourceSnapshot(
        id="snapshot:" + hashlib.sha256(identity.encode()).hexdigest(),
        artifacts=artifacts,
        intake_issues=issues,
    )


def package_hash(package: ScientificPackage) -> str:
    raw = json.dumps(package.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def all_sources(
    package: ScientificPackage | ResearchContext,
    requests: list[ResearcherRequest] | list[ResearcherDecision],
) -> list[Source]:
    if isinstance(package, ResearchContext):
        return list(package.package.sources) + [
            Source(
                id=f"researcher:{d.id}:{response.response_id}",
                title=d.problem,
                kind="researcher_input",
                text=response.text,
            )
            for d in requests
            for response in d.responses
            if response.text and response.action != "CLARIFY"
        ]
    return list(package.sources) + [
        Source(id=f"researcher:{r.id}", title=r.question, kind="researcher_input", text=r.answer)
        for r in requests
        if r.kind == "input" and r.status == "answered" and r.answer is not None
    ]
