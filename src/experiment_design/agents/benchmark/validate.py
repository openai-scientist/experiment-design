"""Deterministic validation. ``verified`` requires metadata that was really fetched."""

from experiment_design.agents.benchmark.models import Resource
from experiment_design.packs import Pack


def validate(resource: Resource) -> Resource:
    if resource.origin == "researcher":
        ok = bool(resource.sha256 and resource.revision)
        reasons = (
            resource.reasons if ok else [*resource.reasons, "descriptor lacks sha256 or version"]
        )
        return resource.model_copy(
            update={"status": "verified" if ok else "unknown", "reasons": reasons}
        )
    if resource.status == "not_found":
        return resource
    if resource.revision and resource.retrieved_at:
        return resource.model_copy(update={"status": "verified"})
    reasons = list(resource.reasons)
    if resource.kind == "baseline" and resource.source_url:
        return resource.model_copy(update={"status": "cited"})
    if (resource.source_url or "").startswith("https://github.com/") and not resource.hf_repo_id:
        reasons.append("the GitHub lookup gave no revision")
    else:
        if not reasons:
            reasons.append("no Hub metadata with a revision was retrieved")
        elif not any("revision" in reason or "Hub" in reason for reason in reasons):
            reasons.append("no revision available")
        if resource.retrieved_at and not resource.revision:
            reasons.append("Hub response has no revision")
    return resource.model_copy(update={"status": "unknown", "reasons": reasons})


def check_minimums(resources: list[Resource], pack: Pack) -> list[str]:
    shortfalls = []
    benchmarks = sum(1 for r in resources if r.kind == "benchmark" and r.status == "verified")
    baselines = sum(
        1 for r in resources if r.kind == "baseline" and r.status in ("verified", "cited")
    )
    if benchmarks < pack.min_benchmarks:
        shortfalls.append(f"only {benchmarks} verified benchmark (minimum {pack.min_benchmarks})")
    if baselines < pack.min_baselines:
        shortfalls.append(
            f"only {baselines} verified or cited baseline (minimum {pack.min_baselines})"
        )
    return shortfalls
