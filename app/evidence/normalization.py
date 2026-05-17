from __future__ import annotations

from app.schemas.evidence import ConnectorResult, EvidenceItem, SourceRecord


def merge_connector_results(results: list[ConnectorResult]) -> tuple[list[SourceRecord], list[EvidenceItem]]:
    """Merge and deduplicate sources and evidence from multiple connectors.

    Deduplication is by ``source_id``.  If two connectors produce the same
    ``source_id`` the first occurrence wins.
    """
    seen_source_ids: set[str] = set()
    seen_evidence_ids: set[str] = set()
    merged_sources: list[SourceRecord] = []
    merged_evidence: list[EvidenceItem] = []

    for result in results:
        for src in result.sources:
            if src.source_id not in seen_source_ids:
                seen_source_ids.add(src.source_id)
                merged_sources.append(src)
        for evi in result.evidence_items:
            if evi.evidence_id not in seen_evidence_ids:
                seen_evidence_ids.add(evi.evidence_id)
                merged_evidence.append(evi)

    return merged_sources, merged_evidence


def compute_connector_coverage(results: list[ConnectorResult]) -> dict[str, str]:
    """Return a mapping of connector_name -> status for the scientific input metadata."""
    coverage: dict[str, str] = {}
    for r in results:
        if r.errors:
            coverage[r.connector_name] = f"partial ({len(r.errors)} errors)"
        elif r.results_returned == 0:
            coverage[r.connector_name] = "no_results"
        else:
            coverage[r.connector_name] = f"ok ({r.results_returned} results)"
    return coverage
