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


class PriceSensitivityScenario(BaseModel):
    """A single price-demand scenario for sensitivity analysis."""

    scenario_name: str = Field(
        ..., description="Short name, e.g. 'parity', 'premium_2x', 'discount_20%'"
    )
    price_vs_competitor: str = Field(
        ..., description="Relative price vs standard of care, e.g. '2x', '1.5x', '0.8x', 'parity'"
    )
    expected_adoption: Literal["very_low", "low", "moderate", "high", "very_high"] = Field(
        ..., description="Expected market adoption at this price point"
    )
    adoption_rationale: str = Field(
        ..., description="Why this adoption level is expected at this price"
    )
    target_payers: list[str] = Field(
        default_factory=list,
        description="Which payers would likely accept this price (e.g. 'private_insurance', 'government', 'self_pay')"
    )
    viability: Literal["not_viable", "marginal", "viable", "attractive"] = Field(
        "viable", description="Commercial viability assessment at this price point"
    )
    source_ids: list[str] = Field(default_factory=list)


class PriceSensitivityAnalysis(BaseModel):
    """Price sensitivity / demand elasticity analysis.
    
    Answers: 'If our drug is X% more expensive, will buyers still purchase it?'
    """

    reference_drug: str | None = Field(
        None, description="The comparator drug used as price reference (standard of care)"
    )
    reference_price: str | None = Field(
        None, description="Reference drug price (e.g. '$500/month', '€1200/year')"
    )
    scenarios: list[PriceSensitivityScenario] = Field(
        default_factory=list,
        description="List of price scenarios from discount to premium"
    )
    price_ceiling: str | None = Field(
        None, description="Maximum price the market can bear, if estimable"
    )
    key_price_drivers: list[str] = Field(
        default_factory=list,
        description="Factors that justify premium pricing (efficacy, safety, convenience)"
    )
    price_barriers: list[str] = Field(
        default_factory=list,
        description="Factors limiting pricing power (generics, budget constraints, alternatives)"
    )
    willingness_to_pay_assessment: str | None = Field(
        None, description="Overall assessment of payer willingness to pay for differentiation"
    )
    conclusion: str = Field(
        ..., description="1-2 sentence summary: can we price at a premium and still capture market?"
    )
    confidence: Literal["low", "medium", "high"] = Field(
        "low", description="Confidence in price sensitivity estimates"
    )
    assumptions: list[str] = Field(default_factory=list)
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
    price_sensitivity_analysis: PriceSensitivityAnalysis | None = Field(
        None,
        description="Price sensitivity / demand elasticity analysis: will buyers purchase at premium prices?"
    )
    commercial_risks: list[CommercialRisk] = Field(default_factory=list)
    differentiation_opportunities: list[str] = Field(default_factory=list)
    market_size_estimate: str | None = None
    confidence: Literal["low", "medium", "high"] = "medium"
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    sources: list[MarketSourceRef] = Field(default_factory=list)
