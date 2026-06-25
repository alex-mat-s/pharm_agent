"""Pydantic schemas for Russian and Eurasian patent analysis.

Per .clinerules/07-ru-eapo-patent-workflow.md requirements.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class MoleculeType(str, Enum):
    """Type of molecule for patent search context."""

    small_molecule = "small_molecule"
    biologic = "biologic"
    antibody = "antibody"
    combination = "combination"
    unknown = "unknown"


class PatentType(str, Enum):
    """Classification of patent type per .clinerules/07."""

    composition_of_matter = "composition_of_matter"
    antibody_or_biologic_sequence = "antibody_or_biologic_sequence"
    salt_polymorph_or_crystal_form = "salt_polymorph_or_crystal_form"
    formulation = "formulation"
    method_of_manufacture = "method_of_manufacture"
    method_of_treatment_or_indication = "method_of_treatment_or_indication"
    dosing_regimen = "dosing_regimen"
    combination_therapy = "combination_therapy"
    biomarker_defined_subgroup = "biomarker_defined_subgroup"
    delivery_device = "delivery_device"
    process_or_intermediate = "process_or_intermediate"
    unknown = "unknown"


class LegalStatus(str, Enum):
    """Legal status of a patent."""

    active = "active"
    expired = "expired"
    lapsed = "lapsed"
    terminated = "terminated"
    pending = "pending"
    withdrawn = "withdrawn"
    rejected = "rejected"
    unknown = "unknown"


class BlockingRisk(str, Enum):
    """Preliminary blocking risk assessment."""

    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class PatentQuery(BaseModel):
    """Query for patent search across RU/EA sources."""

    inn: str
    inn_english: str | None = None
    inn_russian: str | None = None
    inn_synonyms: list[str] = Field(default_factory=list)
    brand_names: list[str] = Field(default_factory=list)
    molecular_target: str | None = None
    indication: str | None = None
    indication_synonyms: list[str] = Field(default_factory=list)
    known_assignees: list[str] = Field(default_factory=list)
    molecule_type: MoleculeType = MoleculeType.unknown
    ipc_codes: list[str] = Field(default_factory=list)
    cpc_codes: list[str] = Field(default_factory=list)
    max_results: int = 50

    def get_all_search_terms(self) -> list[str]:
        """Return all possible search terms for query expansion."""
        terms: list[str] = [self.inn]
        if self.inn_english:
            terms.append(self.inn_english)
        if self.inn_russian:
            terms.append(self.inn_russian)
        terms.extend(self.inn_synonyms)
        terms.extend(self.brand_names)
        if self.molecular_target:
            terms.append(self.molecular_target)
        if self.indication:
            terms.append(self.indication)
        terms.extend(self.indication_synonyms)
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for t in terms:
            if t and t.lower() not in seen:
                seen.add(t.lower())
                unique.append(t)
        return unique


class PatentEvidence(BaseModel):
    """Normalized patent evidence from any source.

    Per .clinerules/07-ru-eapo-patent-workflow.md requirements.
    """

    # Identifiers
    source_id: str  # e.g., "rospatent:RU2123456", "eapo:EA012345"
    source_type: str  # rospatent, fips, eapo, epo_ops, uspto, wipo
    jurisdiction: str  # RU, EA, EP, US, WO

    # Document numbers
    document_number: str
    application_number: str | None = None
    publication_number: str | None = None

    # Content
    title: str
    abstract: str | None = None
    claims_summary: str | None = None  # if available

    # Parties
    applicants: list[str] = Field(default_factory=list)
    patent_holders: list[str] = Field(default_factory=list)
    inventors: list[str] = Field(default_factory=list)

    # Dates
    filing_date: str | None = None
    priority_date: str | None = None
    publication_date: str | None = None
    grant_date: str | None = None

    # Status
    legal_status: LegalStatus = LegalStatus.unknown

    # Classification
    ipc_codes: list[str] = Field(default_factory=list)
    cpc_codes: list[str] = Field(default_factory=list)
    patent_types: list[PatentType] = Field(default_factory=list)

    # Analysis
    relevance_reason: str | None = None
    blocking_risk_preliminary: BlockingRisk = BlockingRisk.unknown

    # Provenance
    source_url: str | None = None
    retrieved_at: str
    raw_metadata: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class PatentFamilyEvidence(BaseModel):
    """Clustered patent family evidence."""

    family_id: str  # Generated or from INPADOC
    priority_number: str | None = None

    # Member patents
    members: list[PatentEvidence] = Field(default_factory=list)
    jurisdictions: list[str] = Field(default_factory=list)  # RU, EA, EP, US, etc.

    # Aggregated info
    earliest_priority_date: str | None = None
    latest_expiration_estimate: str | None = None
    main_applicants: list[str] = Field(default_factory=list)

    # Blocking assessment
    highest_blocking_risk: BlockingRisk = BlockingRisk.unknown
    blocking_jurisdictions: list[str] = Field(default_factory=list)

    # Analysis
    patent_types: list[PatentType] = Field(default_factory=list)
    relevance_summary: str | None = None


class PatentSearchResult(BaseModel):
    """Result of a patent search operation from a single connector."""

    connector_name: str
    query: PatentQuery
    patents: list[PatentEvidence] = Field(default_factory=list)
    total_results_available: int = 0
    results_returned: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    source_available: bool = True
    endpoint: str | None = None  # e.g. ROSPATENT_BASE_URL used


class AggregatedPatentResult(BaseModel):
    """Aggregated result from all patent sources."""

    query: PatentQuery

    # By source
    rospatent_results: PatentSearchResult | None = None
    fips_results: PatentSearchResult | None = None
    eapo_results: PatentSearchResult | None = None
    epo_results: PatentSearchResult | None = None
    wipo_results: PatentSearchResult | None = None
    uspto_results: PatentSearchResult | None = None

    # Aggregated & clustered
    all_patents: list[PatentEvidence] = Field(default_factory=list)
    patent_families: list[PatentFamilyEvidence] = Field(default_factory=list)

    # Diagnostics
    sources_queried: list[str] = Field(default_factory=list)
    sources_available: list[str] = Field(default_factory=list)
    sources_unavailable: list[str] = Field(default_factory=list)
    total_warnings: list[str] = Field(default_factory=list)
    requires_manual_review: bool = False
    manual_review_reasons: list[str] = Field(default_factory=list)


# Disclaimers per .clinerules/07-ru-eapo-patent-workflow.md
PATENT_DISCLAIMER_EN = (
    "This automated patent analysis is preliminary and does not constitute "
    "a legal freedom-to-operate opinion. The results must be reviewed by "
    "a qualified patent attorney before any development, licensing, or "
    "commercialization decision."
)

PATENT_DISCLAIMER_RU = (
    "Данный автоматизированный патентный анализ является предварительным "
    "и не является юридическим заключением о свободе действий. Результаты "
    "должны быть проверены квалифицированным патентным поверенным до принятия "
    "решений о разработке, лицензировании или коммерциализации."
)
