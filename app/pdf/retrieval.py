from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from app.schemas.evidence import (
    ConnectorQuery,
    ConnectorResult,
    EvidenceCategory,
    EvidenceItem,
    SourceRecord,
    SourceType,
)
from app.schemas.pdf import PDFChunk, PDFExtractionResult
from app.schemas.run import StageOutput


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _score_chunk(chunk: PDFChunk, terms: list[str]) -> float:
    text_lower = chunk.text.lower()
    score = 0.0
    for term in terms:
        if term.lower() in text_lower:
            score += 1.0
    return score


def retrieve_pdf_evidence(
    stage_outputs: list[StageOutput],
    query: ConnectorQuery,
) -> ConnectorResult:
    """Score and rank PDF chunks against query terms.

    Loads PDFExtractionResult objects from stored stage outputs and returns
    relevant chunks as normalized SourceRecord + EvidenceItem objects.
    """
    search_terms: list[str] = [query.inn]
    if query.disease:
        search_terms.append(query.disease)
    search_terms.extend(query.synonyms)
    search_terms.extend(query.brand_names)

    sources: list[SourceRecord] = []
    evidence: list[EvidenceItem] = []

    for stage_out in stage_outputs:
        if stage_out.stage != "pdf_extraction":
            continue
        extraction = PDFExtractionResult.model_validate_json(stage_out.output_json)

        for chunk in extraction.chunks:
            score = _score_chunk(chunk, search_terms)
            if score <= 0:
                continue

            source_id = f"pdf:{extraction.pdf_id}:p{chunk.page_number}"
            snippet = chunk.text[:500]

            sources.append(
                SourceRecord(
                    source_id=source_id,
                    source_type=SourceType.local_pdf,
                    title=f"{extraction.pdf_id} — Page {chunk.page_number}",
                    url_or_path=extraction.pdf_id,
                    external_id=f"{extraction.pdf_id}:p{chunk.page_number}",
                    publisher="Local PDF",
                    retrieved_at=_now_iso(),
                    query_used=" ".join(search_terms),
                    raw_payload_hash=extraction.sha256,
                    citation_label=f"[{extraction.pdf_id}, p.{chunk.page_number}]",
                    evidence_summary=snippet,
                )
            )
            evidence.append(
                EvidenceItem(
                    evidence_id=f"evi:pdf:{extraction.pdf_id}:p{chunk.page_number}",
                    source_id=source_id,
                    category=EvidenceCategory.other,
                    summary=snippet,
                    relevance_score=score,
                    confidence="medium",
                )
            )

    evidence.sort(key=lambda e: e.relevance_score, reverse=True)

    return ConnectorResult(
        connector_name="local_pdf",
        query=query,
        sources=sources,
        evidence_items=evidence,
        total_results_available=len(sources),
        results_returned=len(sources),
    )
