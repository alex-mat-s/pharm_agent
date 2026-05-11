from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RawInput(BaseModel):
    """Raw user input for a pharma analysis run."""

    inn_raw: str = Field(..., min_length=1, description="МНН / INN (required)")
    disease_raw: str | None = Field(None, description="Заболевание / indication (optional but recommended)")
    region: str | None = Field(None, description="Регион: global | US | EU | RU | custom")
    molecule_type: str | None = Field(None, description="Тип молекулы или формулировки")
    stage: str | None = Field(None, description="Стадия разработки")


class NormalizedINN(BaseModel):
    """Normalized drug identity."""

    preferred_name: str
    english_inn: str | None = None
    russian_name: str | None = None
    synonyms: list[str] = []
    cas: str | None = None
    atc_codes: list[str] = []
    brand_names: list[str] = []
    molecule_type: str = "unknown"
    confidence: Literal["low", "medium", "high"] = "medium"


class NormalizedDisease(BaseModel):
    """Normalized disease / indication identity."""

    preferred_name: str
    mesh: list[str] = []
    icd_codes: list[str] = []
    synonyms: list[str] = []
    subtypes: list[str] = []
    biomarkers: list[str] = []
    target_population_hypothesis: str | None = None
    confidence: Literal["low", "medium", "high"] = "medium"
