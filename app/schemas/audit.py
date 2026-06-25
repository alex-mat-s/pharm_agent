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
        Literal[
            "llm_call",
            "llm_call_started",
            "llm_call_completed",
            "llm_call_failed",
            "llm_validation_failed",
            "llm_repair_attempted",
            "tool_call",
            "state_change",
            "human_decision",
            "error",
            "stage_started",
            "stage_completed",
            "stage_failed",
            "connector_call",
            "connector_call_started",
            "connector_call_completed",
            "connector_call_failed",
            "pdf_retrieval",
            "pdf_retrieval_started",
            "pdf_retrieval_completed",
            "evidence_normalized",
            "evidence_ranked",
            "obsidian_note_written",
            "sqlite_persisted",
        ],
        Field(...),
    ]
    timestamp: str  # ISO-8601
    status: Annotated[Literal["started", "succeeded", "failed", "blocked"], Field(...)]
    input_ref: str | None = None
    output_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
