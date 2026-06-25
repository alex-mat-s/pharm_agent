"""Pydantic schemas for the Synthesis / Final Investment Memo Agent (MVP 5).

Three-pillar structure:
1. Коммерческая привлекательность — What exists on the market now
2. Научная обоснованность (спрос) — Is there demand? Patient population & market sizing
3. Финансовая жизнеспособность — Patents, FTO, patent fence, investment profile
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# Input Summary
# ═══════════════════════════════════════════════════════════════════════════════


class InputSummary(BaseModel):
    """Summary of the normalized input from earlier stages."""

    run_id: str
    inn_preferred: str
    inn_english: str | None = None
    inn_russian: str | None = None
    disease_preferred: str | None = None
    disease_synonyms: list[str] = Field(default_factory=list)
    region: str | None = None
    molecule_type: str = "unknown"
    development_stage: str | None = None
    target_patient_segment: str | None = None
    pdf_versions_used: dict[str, str] = Field(default_factory=dict)
    human_verification_status: str = "unknown"
    human_verification_timestamp: str | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# Overall Conclusion
# ═══════════════════════════════════════════════════════════════════════════════


class OverallConclusion(BaseModel):
    """Overall go/no-go interpretation with rationale."""

    summary: str = Field(
        ...,
        description="2-4 sentence executive summary of the opportunity.",
    )
    go_no_go_interpretation: Literal["go", "conditional_go", "no_go", "insufficient_evidence"] = Field(
        ...,
        description="High-level interpretation: go, conditional_go, no_go, or insufficient_evidence.",
    )
    main_reason: str = Field(
        ...,
        description="Primary reason supporting the interpretation.",
    )
    critical_dependencies: list[str] = Field(
        default_factory=list,
        description="Key conditions that must be met for the conclusion to hold.",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 1: Коммерческая привлекательность — What exists on the market
# ═══════════════════════════════════════════════════════════════════════════════


class ExistingDrugOnMarket(BaseModel):
    """A drug currently prescribed by doctors for the target indication."""

    drug_name: str = Field(..., description="Drug name (INN or brand)")
    mechanism: str | None = Field(None, description="Mechanism of action")
    strengths: list[str] = Field(default_factory=list, description="Advantages (efficacy, safety, cost, etc.)")
    weaknesses: list[str] = Field(default_factory=list, description="Disadvantages (side effects, cost, inconvenience)")
    market_position: str | None = Field(None, description="Market position: leader, niche, declining, etc.")
    source_ids: list[str] = Field(default_factory=list)


class PipelineCompetitor(BaseModel):
    """A competitor drug in clinical development pipeline."""

    drug_name: str = Field(..., description="Drug or molecule name")
    company: str | None = Field(None, description="Developing company")
    phase: str | None = Field(None, description="Development phase (Phase 1/2/3, filed, etc.)")
    expected_timeline: str | None = Field(None, description="Expected approval or data readout timeline")
    mechanism: str | None = Field(None, description="Mechanism of action")
    competitive_threat: Literal["low", "medium", "high", "critical"] = Field(
        "medium", description="How much this threatens our drug's prospects"
    )
    threat_rationale: str | None = Field(None, description="Why this is a competitive threat")
    source_ids: list[str] = Field(default_factory=list)


class TreatmentStandard(BaseModel):
    """Current gold standard of treatment for the target disease."""

    standard_name: str = Field(..., description="Name of the current gold standard treatment")
    description: str = Field("", description="What makes it the gold standard")
    efficacy_bar: str | None = Field(None, description="Efficacy level our drug must match or exceed")
    key_limitations: list[str] = Field(default_factory=list, description="Limitations of the current standard")
    what_our_drug_must_beat: str | None = Field(
        None, description="Specific aspect(s) our drug must surpass to be adopted"
    )
    source_ids: list[str] = Field(default_factory=list)


class CommercialAttractivenessSynthesis(BaseModel):
    """PILLAR 1: Коммерческая привлекательность.

    Key question: What exists on the market NOW?
    """

    summary: str = Field(
        ..., description="2-4 sentence summary of the competitive landscape"
    )

    # 1. What's already on the market
    existing_drugs: list[ExistingDrugOnMarket] = Field(
        default_factory=list,
        description="Drugs doctors currently prescribe for this indication, with pros/cons",
    )
    existing_drugs_summary: str | None = Field(
        None, description="Summary: what drugs are available now, their strengths and weaknesses"
    )

    # 2. What's coming (competitor pipeline)
    pipeline_competitors: list[PipelineCompetitor] = Field(
        default_factory=list,
        description="Competitor drugs in clinical development (from ClinicalTrials.gov and other sources)",
    )
    pipeline_threat_assessment: str | None = Field(
        None, description="Overall assessment: how threatening is the pipeline? Any competitors 2-3 years ahead?"
    )

    # 3. Treatment standards (gold standard)
    treatment_standard: TreatmentStandard | None = Field(
        None, description="Current gold standard of treatment"
    )
    our_drug_vs_standard: str | None = Field(
        None,
        description="How does our drug compare to the gold standard? What must it beat to be adopted?",
    )

    # Legacy fields for backwards compatibility
    market_opportunity: str | None = None
    competitor_pressure: str | None = None
    payer_value: str | None = None
    price_sensitivity_conclusion: str | None = Field(
        None,
        description="Summary of price sensitivity analysis: can we price at a premium and still sell?"
    )
    premium_pricing_viable: bool | None = Field(
        None,
        description="Is premium pricing (e.g. 1.5-2x competitor) commercially viable?"
    )
    commercial_risks: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 2: Научная обоснованность (Спрос) — Is there demand?
# ═══════════════════════════════════════════════════════════════════════════════


class DiseasePrevalence(BaseModel):
    """Disease prevalence and incidence data."""

    global_prevalence: str | None = Field(None, description="Global prevalence estimate")
    regional_prevalence: str | None = Field(None, description="Prevalence in target region")
    incidence_rate: str | None = Field(None, description="New cases per year")
    trend: Literal["growing", "stable", "declining", "unknown"] = Field(
        "unknown", description="Is prevalence growing, stable, or declining?"
    )
    trend_drivers: list[str] = Field(
        default_factory=list,
        description="Factors driving the trend (aging population, better diagnostics, etc.)"
    )
    source_ids: list[str] = Field(default_factory=list)


class TargetPatientSegment(BaseModel):
    """Specific patient segment for our drug (not all patients with the disease)."""

    segment_description: str = Field(
        ..., description="Description of the target patient segment"
    )
    segment_size_vs_total: str | None = Field(
        None, description="What fraction of total disease patients? E.g. '5% of all lung cancer patients'"
    )
    selection_criteria: list[str] = Field(
        default_factory=list,
        description="Biomarkers, genetic mutations, disease subtype, etc. that define this segment"
    )
    rationale: str | None = Field(
        None, description="Why this is our target segment"
    )
    source_ids: list[str] = Field(default_factory=list)


class MarketDynamicsAssessment(BaseModel):
    """Market dynamics and growth drivers."""

    market_direction: Literal["growing", "stable", "declining", "unknown"] = Field(
        "unknown", description="Is the market for treatments growing or declining?"
    )
    key_drivers: list[str] = Field(
        default_factory=list,
        description="What drives market growth (aging, diagnostics, changing standards, etc.)"
    )
    key_barriers: list[str] = Field(
        default_factory=list,
        description="What limits market growth (generics, budget constraints, etc.)"
    )
    diagnostic_improvement_impact: str | None = Field(
        None, description="Will better diagnostics increase visible patient population?"
    )
    standard_of_care_shifts: str | None = Field(
        None, description="Are treatment standards changing? How does this affect demand?"
    )
    source_ids: list[str] = Field(default_factory=list)


class PayerValueAssessment(BaseModel):
    """Value assessment from different payer perspectives."""

    value_for_physician: str | None = Field(
        None, description="Why would a doctor prescribe this over alternatives?"
    )
    value_for_patient: str | None = Field(
        None, description="Patient benefits: convenience, fewer side effects, etc."
    )
    value_for_payer: str | None = Field(
        None,
        description="Value for insurance/government: cost savings from fewer hospitalizations, etc."
    )
    health_economics_argument: str | None = Field(
        None, description="Long-term cost-effectiveness argument"
    )
    source_ids: list[str] = Field(default_factory=list)


class PricingForecast(BaseModel):
    """Pricing analysis and sales forecast logic."""

    competitor_price_range: str | None = Field(
        None, description="Price range of existing treatments"
    )
    our_price_rationale: str | None = Field(
        None, description="Rationale for our pricing level"
    )
    premium_justification: str | None = Field(
        None,
        description="If 20% more effective but 2x more expensive, would buyers accept? Analysis."
    )
    price_sensitivity_conclusion: str | None = Field(
        None, description="Can we charge a premium and still capture market share?"
    )
    market_size_estimate: str | None = Field(
        None, description="Estimated addressable market size in currency"
    )
    source_ids: list[str] = Field(default_factory=list)


class ScientificRationaleSynthesis(BaseModel):
    """PILLAR 2: Научная обоснованность / Спрос.

    Key question: Is there DEMAND? How many patients, is the drug needed?
    """

    summary: str = Field(
        ..., description="2-4 sentence summary of demand and scientific rationale"
    )

    # 1. Market size and patient population
    disease_prevalence: DiseasePrevalence | None = Field(
        None, description="How many people have this disease?"
    )
    target_patient_segment: TargetPatientSegment | None = Field(
        None,
        description="Our specific target segment (not all patients, but the realistic addressable group)",
    )
    realistic_patient_pool: str | None = Field(
        None,
        description="Realistic number of patients we can treat (after segmentation)",
    )

    # 2. Market dynamics and drivers
    market_dynamics: MarketDynamicsAssessment | None = Field(
        None, description="Is this market growing or declining? Why?"
    )

    # 3. Payer value proposition
    payer_value: PayerValueAssessment | None = Field(
        None, description="Value for physicians, patients, and payers"
    )

    # 4. Pricing and sales forecast
    pricing_forecast: PricingForecast | None = Field(
        None, description="Pricing strategy and market acceptance analysis"
    )

    # Scientific evidence summary (from scientific agent)
    mechanism_summary: str | None = Field(
        None, description="Brief summary of mechanism of action"
    )
    unmet_need_summary: str | None = Field(
        None, description="What unmet medical need does this drug address?"
    )

    # Legacy fields for backwards compatibility
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 3: Финансовая жизнеспособность — Patents, FTO, Patent Fence
# ═══════════════════════════════════════════════════════════════════════════════


class FTOCheckResult(BaseModel):
    """Freedom-to-Operate check result."""

    active_blocking_patents_found: int | None = Field(
        None, description="Number of potentially blocking active patents found"
    )
    total_relevant_patents: int | None = Field(
        None, description="Total relevant patents in the landscape"
    )
    composition_patents: str | None = Field(
        None, description="Patents on the active substance itself (strongest protection)"
    )
    process_patents: str | None = Field(
        None, description="Patents on manufacturing / chemical synthesis"
    )
    indication_patents: str | None = Field(
        None, description="Patents on treating the specific disease"
    )
    formulation_patents: str | None = Field(
        None, description="Patents on specific dosage forms"
    )
    fto_risk_level: Literal["low", "medium", "high", "critical", "unknown"] = Field(
        "unknown", description="Overall FTO risk level"
    )
    fto_conclusion: str | None = Field(
        None, description="Can we proceed without infringing? What are the risks?"
    )
    more_patents_means_less_attractive: str | None = Field(
        None,
        description="Assessment: does the number of patents make this less attractive for development?"
    )
    source_ids: list[str] = Field(default_factory=list)


class PatentExpiryImpact(BaseModel):
    """Impact of competitor patent expiries on the market."""

    drug_name: str = Field(..., description="Drug whose patent is expiring")
    patent_expiry_date: str | None = Field(None, description="When does the patent expire?")
    generic_entry_expected: str | None = Field(None, description="When are generics/biosimilars expected?")
    market_impact: str | None = Field(
        None, description="How will this change the market (price drop, new competition)?  "
    )
    opportunity_or_threat: Literal["opportunity", "threat", "neutral", "unknown"] = Field(
        "unknown", description="Is this an opportunity or threat for our drug?"
    )
    source_ids: list[str] = Field(default_factory=list)


class PatentFenceStrategy(BaseModel):
    """Strategy for building our own patent protection."""

    primary_patent: str | None = Field(
        None, description="Primary patent on the molecule (~20 years protection from filing)"
    )
    secondary_patents: list[str] = Field(
        default_factory=list,
        description="Secondary patents: new use, combination, formulation, etc. that extend protection",
    )
    total_protection_window: str | None = Field(
        None, description="Estimated total protection window (primary + secondary)"
    )
    patent_fence_feasibility: Literal["low", "medium", "high", "unknown"] = Field(
        "unknown", description="How feasible is building a strong patent fence?"
    )
    strategy_summary: str | None = Field(
        None,
        description="Summary: how to protect our invention and extend monopoly",
    )
    source_ids: list[str] = Field(default_factory=list)


class PatentFinancialViabilitySynthesis(BaseModel):
    """PILLAR 3: Финансовая жизнеспособность.

    Key question: Is the path clear for development and commercialization?
    How to protect our monopoly for as long as possible?
    """

    summary: str = Field(
        ..., description="2-4 sentence summary of patent landscape and financial viability"
    )

    # 1. FTO Check — do we infringe others' patents?
    fto_check: FTOCheckResult | None = Field(
        None, description="Freedom-to-Operate analysis: are we infringing active patents?"
    )

    # 2. Competitor patent expiry impact
    patent_expiry_impacts: list[PatentExpiryImpact] = Field(
        default_factory=list,
        description="Impact of competitor patent expiries on the market"
    )

    # 3. Our patent fence strategy
    patent_fence: PatentFenceStrategy | None = Field(
        None, description="Strategy for building patent protection around our drug"
    )

    # Legacy fields for backwards compatibility
    fto_risks: list[str] = Field(default_factory=list)
    patent_fence_opportunities: list[str] = Field(default_factory=list)
    investment_range: InvestmentRange | None = None
    monetization_timeline: MonetizationTimeline | None = None
    source_ids: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# Supporting models
# ═══════════════════════════════════════════════════════════════════════════════


class InvestmentRange(BaseModel):
    """Investment range across scenarios."""

    low_case: str = Field(..., description="Low investment scenario, e.g. '$5M-$10M'")
    base_case: str = Field(..., description="Base investment scenario, e.g. '$15M-$30M'")
    high_case: str = Field(..., description="High investment scenario, e.g. '$50M-$100M'")
    currency: str = "USD"
    assumptions: list[str] = Field(
        default_factory=list,
        description="Key assumptions underlying the investment range.",
    )


class MonetizationTimeline(BaseModel):
    """Timeline for potential monetization events."""

    earliest_value_inflection: str | None = Field(
        None,
        description="Earliest point at which significant value may be realized.",
    )
    licensing_window: str | None = Field(
        None,
        description="Time frame for potential licensing deals.",
    )
    revenue_window: str | None = Field(
        None,
        description="Time frame for potential revenue generation.",
    )
    required_evidence_for_monetization: list[str] = Field(
        default_factory=list,
        description="Evidence milestones required to unlock monetization.",
    )
    key_risks: list[str] = Field(
        default_factory=list,
        description="Key risks to the monetization timeline.",
    )


class Contradiction(BaseModel):
    """A contradiction identified between previous stage outputs."""

    area: str = Field(..., description="Area where contradiction exists.")
    description: str = Field(..., description="Description of the contradiction.")
    affected_conclusion: str = Field(..., description="Which conclusion is affected.")
    severity: Literal["low", "medium", "high"] = Field(
        "medium",
        description="Severity of the contradiction.",
    )
    source_agent_outputs: list[str] = Field(
        default_factory=list,
        description="Which agent outputs are involved.",
    )


class SourceAvailabilityWarning(BaseModel):
    """Warning about unavailable or incomplete data sources."""

    source_name: str = Field(..., description="Name of the source.")
    warning_type: Literal["unavailable", "partial", "stale", "error"] = "unavailable"
    description: str = Field(..., description="Description of the availability issue.")
    impact_on_analysis: str | None = Field(
        None,
        description="How this affects the analysis conclusions.",
    )


class ManualReviewItem(BaseModel):
    """Item requiring manual expert review."""

    area: str = Field(..., description="Area requiring review.")
    reason: str = Field(..., description="Why manual review is needed.")
    recommended_expert_type: str = Field(
        ...,
        description="Type of expert needed.",
    )
    priority: Literal["low", "medium", "high"] = Field(
        "medium",
        description="Priority of the review.",
    )


class NextStep(BaseModel):
    """Recommended next step for the analysis or decision process."""

    action: str = Field(..., description="Description of the action.")
    rationale: str = Field(..., description="Why this action is recommended.")
    responsible_party: str | None = Field(None, description="Who should perform this.")
    priority: Literal["low", "medium", "high"] = "medium"
    timeline: str | None = None


class SourceReference(BaseModel):
    """Reference to a source used in the synthesis."""

    source_id: str
    source_type: str = "unknown"
    title: str = ""
    used_in_sections: list[str] = Field(default_factory=list)
    citation_label: str = ""


class Disclaimer(BaseModel):
    """Disclaimer for the final report."""

    category: Literal[
        "medical",
        "legal",
        "financial",
        "patent",
        "general",
    ] = "general"
    text: str


# ═══════════════════════════════════════════════════════════════════════════════
# Final Synthesis Output
# ═══════════════════════════════════════════════════════════════════════════════


class FinalSynthesisOutput(BaseModel):
    """Complete output of the Synthesis / QA Agent.

    Three pillars:
    1. commercial_attractiveness — What exists on the market (competition, pipeline, gold standard)
    2. scientific_rationale — Is there demand? (prevalence, segments, dynamics, payer value, pricing)
    3. patent_and_financial_viability — Is the path clear? (FTO, patent fence, investment)
    """

    run_id: str
    input_summary: InputSummary
    overall_conclusion: OverallConclusion

    # Three pillars
    commercial_attractiveness: CommercialAttractivenessSynthesis
    scientific_rationale: ScientificRationaleSynthesis
    patent_and_financial_viability: PatentFinancialViabilitySynthesis

    contradictions: list[Contradiction] = Field(default_factory=list)
    source_availability_warnings: list[SourceAvailabilityWarning] = Field(default_factory=list)
    manual_review_required: list[ManualReviewItem] = Field(default_factory=list)
    next_steps: list[NextStep] = Field(default_factory=list)

    source_references: list[SourceReference] = Field(default_factory=list)
    disclaimers: list[Disclaimer] = Field(default_factory=list)

    created_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="ISO timestamp of synthesis completion.",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Input schema for the Synthesis Agent
# ═══════════════════════════════════════════════════════════════════════════════


class SynthesisAgentInput(BaseModel):
    """Input data package for the Synthesis Agent."""

    run_id: str

    # Normalized input from MVP 1
    inn_preferred: str
    inn_english: str | None = None
    inn_russian: str | None = None
    inn_synonyms: list[str] = Field(default_factory=list)
    disease_preferred: str | None = None
    disease_synonyms: list[str] = Field(default_factory=list)
    region: str | None = None
    molecule_type: str = "unknown"
    stage: str | None = None
    target_patient_segment: str | None = None

    # Human verification
    human_verification_status: str
    human_verification_timestamp: str | None = None
    human_verification_comments: str | None = None

    # PDF metadata
    pdf_hashes: dict[str, str] = Field(default_factory=dict)

    # Previous agent outputs (JSON strings)
    scientific_output_json: str | None = None
    market_output_json: str | None = None
    patent_finance_output_json: str | None = None

    # Source registry
    source_registry_json: str | None = None

    # Source warnings collected from connectors
    source_warnings: list[str] = Field(default_factory=list)

    # Pre-detected contradictions (from deterministic checks)
    detected_contradictions: list[dict] = Field(default_factory=list)


class SynthesisPreconditionError(Exception):
    """Raised when synthesis preconditions are not met."""

    pass
