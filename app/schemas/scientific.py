from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SourceClaim(BaseModel):
    """A single claim linked to one or more source_ids."""

    claim: str
    source_ids: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"


class ApprovedTherapy(BaseModel):
    """An approved therapy entry from regulatory evidence."""

    name: str
    regulatory_status: str = ""
    source_ids: list[str] = Field(default_factory=list)


class ClinicalTrialEntry(BaseModel):
    """A clinical trial entry from ClinicalTrials.gov or other sources."""

    nct_id: str | None = None
    title: str = ""
    phase: str = ""
    status: str = ""
    sponsor: str = ""
    conditions: list[str] = Field(default_factory=list)
    interventions: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class ScientificAgentInput(BaseModel):
    """Input bundle for the Scientific Agent LLM call."""

    run_id: str
    inn_preferred: str
    inn_english: str | None = None
    inn_synonyms: list[str] = Field(default_factory=list)
    disease_preferred: str | None = None
    disease_synonyms: list[str] = Field(default_factory=list)
    region: str | None = None
    pdf_hashes: dict[str, str] = Field(default_factory=dict)
    evidence_items_json: str = "[]"
    sources_json: str = "[]"
    connector_coverage: dict[str, str] = Field(default_factory=dict)


class ScientificAgentOutput(BaseModel):
    """Structured output of the Scientific Agent."""

    executive_summary: str = ""
    mechanism_of_action: SourceClaim | None = None
    disease_pathophysiology: SourceClaim | None = None
    mechanistic_rationale: SourceClaim | None = None
    existing_evidence: list[SourceClaim] = Field(default_factory=list)
    standard_of_care: SourceClaim | None = None
    approved_therapies: list[ApprovedTherapy] = Field(default_factory=list)
    clinical_trial_landscape: list[ClinicalTrialEntry] = Field(default_factory=list)
    safety_considerations: list[SourceClaim] = Field(default_factory=list)
    unmet_medical_need: SourceClaim | None = None
    scientific_risks: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    source_ids_used: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"

    disclaimer: str = (
        "This report is an AI-assisted R&D research artifact. "
        "It is not medical advice, not regulatory advice, and not a "
        "substitute for expert clinical, regulatory, or legal review."
    )
