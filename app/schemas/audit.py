from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    """Base audit event for JSONL append-only log and SQLite audit table.

    Supports generic events plus rich LLM call metadata in ``metadata``.
    """

    event_id: str  # UUID
    run_id: str
    stage: str
    event_type: Annotated[
        Literal["llm_call", "tool_call", "state_change", "human_decision", "error"],
        Field(...),
    ]
    timestamp: str  # ISO-8601
    status: Annotated[Literal["started", "succeeded", "failed"], Field(...)]
    input_ref: str | None = None
    output_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
