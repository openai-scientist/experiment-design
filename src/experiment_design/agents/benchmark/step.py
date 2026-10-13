"""Step ``s2b_resources``: survey, select, acquire, validate and count minimums."""

from collections.abc import Callable
from datetime import datetime

from experiment_design.agents.benchmark.acquire import acquire
from experiment_design.agents.benchmark.hf import HFClient
from experiment_design.agents.benchmark.models import Resource, ResourcesDoc
from experiment_design.agents.benchmark.select import select
from experiment_design.agents.benchmark.survey import researcher_resources, survey
from experiment_design.agents.benchmark.validate import check_minimums, validate
from experiment_design.config import RunConfig
from experiment_design.packs import Pack
from experiment_design.persistence.store import RunStore
from experiment_design.runtime.llm import LLMClient

STEP_KEY = "s2b_resources"


def _cell(value: object) -> str:
    return str(value if value not in (None, "") else "-").replace("|", r"\|").replace("\n", " ")


def render(doc: ResourcesDoc) -> str:
    lines = [
        "# Resources",
        "",
        "| Kind | Name | Origin | Status | Revision | License | Contamination | Source |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in doc.resources:
        lines.append(
            "| "
            + " | ".join(
                _cell(v)
                for v in (
                    r.kind,
                    r.name,
                    r.origin,
                    r.status,
                    r.revision,
                    r.license,
                    r.contamination_risk,
                    r.source_url,
                )
            )
            + " |"
        )
    notes = [f"- {r.name}: {reason}" for r in doc.resources for reason in r.reasons]
    if notes:
        lines += ["", "## Notes", *notes]
    if doc.shortfalls:
        lines += ["", "## Limitations", *(f"- {item}" for item in doc.shortfalls)]
    return "\n".join(lines) + "\n"


def run_resources(
    store: RunStore,
    llm: LLMClient,
    cfg: RunConfig,
    pack: Pack,
    hf: HFClient,
    *,
    comments: str = "",
    now: Callable[[], datetime] = datetime.now,
) -> ResourcesDoc:
    brief = store.read(2, "research_brief.md")
    hypotheses = store.read(1, "hypothesis.md")

    def announce(kind: str, actor: str, items: list[Resource]) -> None:
        store.activity(
            f"exd.resources.{kind}",
            actor=actor,
            summary=f"{actor} processed {len(items)} resources",
            step_key=STEP_KEY,
            payload={"count": len(items)},
        )

    candidates = survey(
        store, llm, cfg, pack, hf, brief, hypotheses, researcher_resources(store.sources())
    )
    announce("surveyed", "surveyor", candidates)
    chosen = select(store, llm, cfg, pack, candidates, brief, comments, hypotheses)
    announce("selected", "selector", chosen)
    fetched = [acquire(resource, hf, now) for resource in chosen]
    announce("acquired", "acquirer", fetched)
    checked = [validate(resource) for resource in fetched]
    announce("validated", "validator", checked)
    doc = ResourcesDoc(
        resources=checked,
        minimums={"benchmark": pack.min_benchmarks, "baseline": pack.min_baselines},
        shortfalls=check_minimums(checked, pack),
    )
    store.write(3, "resources.json", doc.model_dump_json(by_alias=True, indent=2))
    store.write(3, "resources.md", render(doc))
    return doc
