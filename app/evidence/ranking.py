from __future__ import annotations

from app.schemas.evidence import EvidenceCategory, EvidenceItem

_CATEGORY_WEIGHTS: dict[EvidenceCategory, float] = {
    EvidenceCategory.clinical_trial: 3.0,
    EvidenceCategory.regulatory: 2.5,
    EvidenceCategory.guideline: 2.5,
    EvidenceCategory.review: 2.0,
    EvidenceCategory.mechanism: 1.5,
    EvidenceCategory.safety: 1.5,
    EvidenceCategory.standard_of_care: 2.0,
    EvidenceCategory.preclinical: 1.0,
    EvidenceCategory.epidemiology: 1.0,
    EvidenceCategory.other: 0.5,
}

_CONFIDENCE_WEIGHTS: dict[str, float] = {
    "high": 2.0,
    "medium": 1.0,
    "low": 0.5,
}


def rank_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Sort evidence by composite score (category weight * confidence * relevance)."""
    def _score(item: EvidenceItem) -> float:
        cat_w = _CATEGORY_WEIGHTS.get(item.category, 0.5)
        conf_w = _CONFIDENCE_WEIGHTS.get(item.confidence, 1.0)
        return cat_w * conf_w * max(item.relevance_score, 0.1)

    return sorted(items, key=_score, reverse=True)
