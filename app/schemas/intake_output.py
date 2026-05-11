from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.input import NormalizedDisease, NormalizedINN


class PDFSectionRef(BaseModel):
    """Reference to a relevant section inside a PDF."""

    pdf_id: str
    page: int
    snippet: str


class IntakeEnrichmentOutput(BaseModel):
    """Structured output of the intake enrichment agent."""

    normalized_inn: NormalizedINN
    normalized_disease: NormalizedDisease | None = None
    ambiguities: list[str] = []
    assumptions: list[str] = []
    missing_information: list[str] = []
    human_questions: list[str] = []
    pdf_relevant_sections: list[PDFSectionRef] = []
    requires_human_review: bool = True
    completeness: Literal["low", "medium", "high"] = "medium"


class HumanVerificationPacket(BaseModel):
    """Packet presented to the user for human verification after intake enrichment."""

    run_id: str
    raw_inn: str
    raw_disease: str | None = None
    normalized_inn: NormalizedINN
    normalized_disease: NormalizedDisease | None = None
    ambiguities: list[str] = []
    assumptions: list[str] = []
    missing_information: list[str] = []
    questions: list[str] = []
    pdf_extraction_status: dict[str, str] = Field(default_factory=dict, description="pdf_id -> status")
    completeness: Literal["low", "medium", "high"] = "medium"
