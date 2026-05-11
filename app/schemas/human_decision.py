from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HumanDecision(BaseModel):
    """Human verification decision after intake enrichment."""

    run_id: str
    decision: Literal["approved", "rejected", "needs_revision"]
    corrections: dict[str, str] = {}
    comments: str | None = None
    reviewer_name: str | None = None
    timestamp: str  # ISO-8601
