"""Pydantic schemas for the Patent/Finance Agent (MVP 4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BlockingPatent(BaseModel):
    """A potentially blocking patent."""

    patent_number: str
    title: str = ""
    assignee: str | None = None
    priority_date: str | None = None
    expiration_date: str | None = None
    patent_type: str | None = None  # composition, formulation, method, etc.
    blocking_rationale: str = ""
    source_ids: list[str] = Field(default_factory=list)


class PatentAssignee(BaseModel):
    """A patent assignee (company/organization)."""

    name: str
    patent_count: int = 0
    key_patents: list[str] = Field(default_factory=list)


class FTORisk(BaseModel):
    """Freedom-to-Operate risk."""

    risk_description: str
    severity: Literal["low", "medium", "high"] = "medium"
    mitigation_strategy: str | None = None
    requires_legal_review: bool = True
    source_ids: list[str] = Field(default_factory=list)


class PatentFenceOpportunity(BaseModel):
    """Opportunity for patent fence strategy."""

    opportunity_description: str
    patent_type: str  # formulation, dosing, combination, etc.
    feasibility: Literal["low", "medium", "high"] = "medium"
    source_ids: list[str] = Field(default_factory=list)


class InvestmentScenario(BaseModel):
    """Investment scenario with amount and assumptions."""

    amount_usd: str  # e.g., "$10M-$50M" or "~$30M"
    assumptions: list[str] = Field(default_factory=list)


class InvestmentRange(BaseModel):
    """Investment range across low/base/high scenarios."""

    low_case: InvestmentScenario
    base_case: InvestmentScenario
    high_case: InvestmentScenario


class MoneyTimeline(BaseModel):
    """Timeline for monetization opportunities."""

    earliest_value_inflection: str | None = None  # e.g., "After Phase 1"
    licensing_window: str | None = None
    approval_window: str | None = None
    revenue_window: str | None = None
    monetization_scenarios: list[str] = Field(default_factory=list)


class FinancialRisk(BaseModel):
    """A financial risk."""

    risk: str
    severity: Literal["low", "medium", "high"] = "medium"
    mitigation: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class PatentFinanceSourceRef(BaseModel):
    """Reference to a source used in patent/finance analysis."""

    source_id: str
    source_type: str = "unknown"
    title: str = ""
    url_or_path: str | None = None
    citation_label: str = ""


class PatentFinanceAgentInput(BaseModel):
    """Input for the patent/finance agent."""

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
    mechanism_of_action: str | None = None
    approved_therapies_json: str | None = None

    # From market agent
    market_summary: str | None = None
    competitors_json: str | None = None
    market_size_estimate: str | None = None

    # Evidence context
    evidence_items_json: str | None = None
    sources_json: str | None = None
    pdf_hashes: dict[str, str] = Field(default_factory=dict)

    # Direct PDF context for patent/finance analysis
    # (PDFs may contain patent documents, financial reports, due diligence materials)
    pdf_context: str | None = None


class PatentFinanceAgentOutput(BaseModel):
    """Structured output of the patent/finance agent."""

    # Patent landscape
    patent_landscape_summary: str
    blocking_patent_candidates: list[BlockingPatent] = Field(default_factory=list)
    patent_count_by_family: dict[str, int] = Field(default_factory=dict)
    main_assignees: list[PatentAssignee] = Field(default_factory=list)
    earliest_priority_dates: list[str] = Field(default_factory=list)
    expected_expirations: list[str] = Field(default_factory=list)
    freedom_to_operate_risks: list[FTORisk] = Field(default_factory=list)
    patent_fence_opportunities: list[PatentFenceOpportunity] = Field(default_factory=list)
    generic_or_biosimilar_risk: str | None = None

    # Financial viability
    investment_range: InvestmentRange
    major_cost_buckets: list[str] = Field(default_factory=list)
    money_timeline: MoneyTimeline
    key_financial_risks: list[FinancialRisk] = Field(default_factory=list)

    # Metadata
    confidence: Literal["low", "medium", "high"] = "medium"
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    legal_review_required: bool = True
    sources: list[PatentFinanceSourceRef] = Field(default_factory=list)

    disclaimer: str = (
        "This is an AI-assisted preliminary patent landscape and financial estimate, "
        "not a legal FTO opinion or investment advice. "
        "Review by a qualified patent attorney and financial analyst is required before business decisions."
    )
