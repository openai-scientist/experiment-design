"""Keyword lookup returns excerpts from supplied sources only."""

import re

from experiment_design.contracts import Record, Source


class EvidenceHit(Record):
    source_id: str
    title: str
    excerpt: str


def lookup_evidence(query: str, sources: list[Source], limit: int = 5) -> list[EvidenceHit]:
    terms = {term for term in re.findall(r"\w+", query.casefold()) if len(term) > 2}
    ranked = []
    for source in sources:
        for paragraph in re.split(r"\n\s*\n", source.text):
            words = set(re.findall(r"\w+", paragraph.casefold()))
            score = len(terms & words)
            if score:
                ranked.append(
                    (
                        score,
                        EvidenceHit(
                            source_id=source.id,
                            title=source.title,
                            excerpt=paragraph.strip()[:600],
                        ),
                    )
                )
    ranked.sort(key=lambda pair: -pair[0])
    return [hit for _, hit in ranked[: max(0, limit)]]
