from __future__ import annotations

from app.schemas.evidence import SourceRecord


def build_citation_list(sources: list[SourceRecord]) -> list[dict[str, str]]:
    """Build a numbered citation list from source records.

    Returns a list of dicts with ``number``, ``source_id``, and ``label`` keys.
    """
    citations: list[dict[str, str]] = []
    for i, src in enumerate(sources, start=1):
        citations.append({
            "number": str(i),
            "source_id": src.source_id,
            "label": src.citation_label or src.title,
        })
    return citations
