from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    pubmed = "pubmed"
    clinicaltrials = "clinicaltrials"
    fda = "fda"
    ema = "ema"
    local_pdf = "local_pdf"
    orange_book = "orange_book"
    purple_book = "purple_book"
    epo_ops = "epo_ops"
    uspto = "uspto"
    wipo = "wipo"
    # Russian and Eurasian patent sources
    rospatent = "rospatent"
    fips = "fips"
    fips_registers = "fips_registers"
    eapo = "eapo"
    eapo_bulletin = "eapo_bulletin"
    google_patents = "google_patents"


class ConnectorQuery(BaseModel):
    """Normalized query sent to every connector."""

    inn: str
    disease: str | None = None
    region: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    brand_names: list[str] = Field(default_factory=list)
    mesh_terms: list[str] = Field(default_factory=list)
    max_results: int = 20


class SourceRecord(BaseModel):
    """Canonical source object shared across all connectors."""

    source_id: str
    source_type: SourceType
    title: str
    url_or_path: str | None = None
    external_id: str | None = None
    publisher: str | None = None
    publication_date: str | None = None
    last_updated_date: str | None = None
    retrieved_at: str
    query_used: str
    raw_payload_hash: str | None = None
    citation_label: str = ""
    evidence_summary: str = ""
    reliability_notes: str = ""


class EvidenceCategory(str, Enum):
    mechanism = "mechanism"
    clinical_trial = "clinical_trial"
    preclinical = "preclinical"
    safety = "safety"
    regulatory = "regulatory"
    standard_of_care = "standard_of_care"
    epidemiology = "epidemiology"
    review = "review"
    guideline = "guideline"
    patent = "patent"
    competitive_landscape = "competitive_landscape"
    other = "other"


class EvidenceItem(BaseModel):
    """Normalized evidence linked to a SourceRecord."""

    evidence_id: str
    source_id: str
    category: EvidenceCategory = EvidenceCategory.other
    summary: str = ""
    key_findings: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"
    relevance_score: float = 0.0


class ConnectorResult(BaseModel):
    """Normalized output of a single connector search."""

    connector_name: str
    query: ConnectorQuery
    sources: list[SourceRecord] = Field(default_factory=list)
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    total_results_available: int = 0
    results_returned: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int | None = None


class ConnectorCallLog(BaseModel):
    """Metadata about a single connector invocation for audit/persistence."""

    run_id: str
    connector_name: str
    query_json: str
    status: Literal["succeeded", "failed", "partial"]
    results_returned: int = 0
    errors: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    timestamp: str
