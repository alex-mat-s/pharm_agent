"""Tests for evidence normalization, ranking, and citation utilities."""
from __future__ import annotations

from app.evidence.citations import build_citation_list
from app.evidence.normalization import compute_connector_coverage, merge_connector_results
from app.evidence.ranking import rank_evidence
from app.schemas.evidence import (
    ConnectorQuery,
    ConnectorResult,
    EvidenceCategory,
    EvidenceItem,
    SourceRecord,
    SourceType,
)


def _query() -> ConnectorQuery:
    return ConnectorQuery(inn="aspirin")


def _source(sid: str, stype: SourceType = SourceType.pubmed) -> SourceRecord:
    return SourceRecord(
        source_id=sid,
        source_type=stype,
        title=f"Title for {sid}",
        retrieved_at="2026-01-01T00:00:00+00:00",
        query_used="aspirin",
        citation_label=f"Citation {sid}",
    )


def _evidence(eid: str, sid: str, cat: EvidenceCategory = EvidenceCategory.other, score: float = 1.0) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=eid,
        source_id=sid,
        category=cat,
        summary=f"Evidence {eid}",
        relevance_score=score,
    )


def test_merge_deduplicates_sources():
    r1 = ConnectorResult(
        connector_name="pubmed",
        query=_query(),
        sources=[_source("src:1"), _source("src:2")],
        evidence_items=[_evidence("e1", "src:1"), _evidence("e2", "src:2")],
        results_returned=2,
    )
    r2 = ConnectorResult(
        connector_name="fda",
        query=_query(),
        sources=[_source("src:2"), _source("src:3")],
        evidence_items=[_evidence("e2", "src:2"), _evidence("e3", "src:3")],
        results_returned=2,
    )

    sources, evidence = merge_connector_results([r1, r2])

    assert len(sources) == 3
    assert {s.source_id for s in sources} == {"src:1", "src:2", "src:3"}
    assert len(evidence) == 3


def test_merge_empty_results():
    r1 = ConnectorResult(connector_name="empty", query=_query())
    sources, evidence = merge_connector_results([r1])
    assert sources == []
    assert evidence == []


def test_coverage_reports_status():
    results = [
        ConnectorResult(connector_name="pubmed", query=_query(), results_returned=5),
        ConnectorResult(connector_name="fda", query=_query(), errors=["timeout"]),
        ConnectorResult(connector_name="ema", query=_query(), results_returned=0),
    ]

    coverage = compute_connector_coverage(results)

    assert "ok" in coverage["pubmed"]
    assert "partial" in coverage["fda"]
    assert coverage["ema"] == "no_results"


def test_rank_evidence_prefers_clinical_trials():
    e1 = _evidence("e1", "s1", EvidenceCategory.other, score=1.0)
    e2 = _evidence("e2", "s2", EvidenceCategory.clinical_trial, score=1.0)
    e3 = _evidence("e3", "s3", EvidenceCategory.regulatory, score=1.0)

    ranked = rank_evidence([e1, e2, e3])

    assert ranked[0].evidence_id == "e2"
    assert ranked[1].evidence_id == "e3"
    assert ranked[2].evidence_id == "e1"


def test_rank_evidence_empty():
    assert rank_evidence([]) == []


def test_build_citation_list():
    sources = [_source("s1"), _source("s2")]
    citations = build_citation_list(sources)
    assert len(citations) == 2
    assert citations[0]["number"] == "1"
    assert citations[0]["source_id"] == "s1"
    assert citations[1]["number"] == "2"
