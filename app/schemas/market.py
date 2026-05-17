"""Pydantic schemas for the Market Attractiveness Agent (MVP 3)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PatientPopulation(BaseModel):
    """Estimated patient population by region."""

    global_estimate: str | None = None
    us_estimate: str | None = None
    eu_estimate: str | None = None
    ru_estimate: str | None = None
    target_segment: str | None = None
    segmentation_logic: str | None = None


class CompetitorEntry(BaseModel):
    """A single competitor drug in the market."""

    drug_name: str
    inn: str | None = None
    company: str | None = None
    status: str = "unknown"
    mechanism: str | None = None
    differentiation: str | None = None
    price_range: str | None = None
    market_share_hint: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class MarketDynamic(BaseModel):
    """A market dynamic or trend affecting the segment."""

    description: str
    direction: Literal["positive", "negative", "neutral"] = "neutral"
    timeframe: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class PriceBenchmark(BaseModel):
    """Competitor or reference price benchmark."""

    drug_name: str
    price_description: str
    currency: str = "USD"
    route: str | None = None
    frequency: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class CommercialRisk(BaseModel):
    """A commercial risk affecting market entry."""

    risk: str
    severity: Literal["low", "medium", "high"] = "medium"
    mitigation: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class MarketSourceRef(BaseModel):
    """Reference to a source used in market analysis."""

    source_id: str
    source_type: str = "unknown"
    title: str = ""
    url_or_path: str | None = None
    citation_label: str = ""


class MarketAgentInput(BaseModel):
    """Input for the market attractiveness agent."""

    run_id: str
    inn_preferred: str
    inn_english: str | None = None
    inn_synonyms: list[str] = Field(default_factory=list)
    disease_preferred: str | None = None
    disease_synonyms: list[str] = Field(default_factory=list)
    region: str | None = None
    molecule_type: str = "unknown"
    stage: str | None = None

    # From scientific agent
    scientific_summary: str | None = None
    approved_therapies_json: str | None = None
    clinical_pipeline_json: str | None = None
    unmet_need: str | None = None

    # Evidence context
    evidence_items_json: str | None = None
    sources_json: str | None = None
    pdf_hashes: dict[str, str] = Field(default_factory=dict)


class MarketAgentOutput(BaseModel):
    """Structured output of the market attractiveness agent."""

    market_summary: str
    patient_population: PatientPopulation
    treatment_landscape: str | None = None
    competitors: list[CompetitorEntry] = Field(default_factory=list)
    market_dynamics: list[MarketDynamic] = Field(default_factory=list)
    payer_value: str | None = None
    pricing_logic: str | None = None
    competitor_price_benchmarks: list[PriceBenchmark] = Field(default_factory=list)
    commercial_risks: list[CommercialRisk] = Field(default_factory=list)
    differentiation_opportunities: list[str] = Field(default_factory=list)
    market_size_estimate: str | None = None
    confidence: Literal["low", "medium", "high"] = "medium"
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    sources: list[MarketSourceRef] = Field(default_factory=list)
