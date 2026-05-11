from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    created = "created"
    input_collected = "input_collected"
    pdfs_registered = "pdfs_registered"
    pdfs_ingested = "pdfs_ingested"
    intake_enriched = "intake_enriched"
    awaiting_human_verification = "awaiting_human_verification"
    human_approved = "human_approved"
    human_rejected = "human_rejected"
    needs_revision = "needs_revision"
    completed = "completed"
    failed = "failed"


# Valid transition graph used by orchestrator and DB.
TRANSITIONS: dict[RunStatus, list[RunStatus]] = {
    RunStatus.created: [RunStatus.input_collected, RunStatus.failed],
    RunStatus.input_collected: [RunStatus.pdfs_registered, RunStatus.failed],
    RunStatus.pdfs_registered: [RunStatus.pdfs_ingested, RunStatus.failed],
    RunStatus.pdfs_ingested: [RunStatus.intake_enriched, RunStatus.failed],
    RunStatus.intake_enriched: [RunStatus.awaiting_human_verification, RunStatus.failed],
    RunStatus.awaiting_human_verification: [
        RunStatus.human_approved,
        RunStatus.human_rejected,
        RunStatus.needs_revision,
        RunStatus.failed,
    ],
    RunStatus.human_approved: [RunStatus.completed],
    RunStatus.human_rejected: [RunStatus.failed],
    RunStatus.needs_revision: [RunStatus.input_collected],
}


def is_valid_transition(from_status: RunStatus, to_status: RunStatus) -> bool:
    return to_status in TRANSITIONS.get(from_status, [])


class RunRecord(BaseModel):
    """Minimal run record for SQLite + in-memory."""

    run_id: str
    status: RunStatus = RunStatus.created
    created_at: str  # ISO-8601
    updated_at: str  # ISO-8601
    raw_input_json: str
    enrichment_output_json: str | None = None
    human_decision_json: str | None = None
    final_summary_json: str | None = None
    error_message: str | None = None


class StageOutput(BaseModel):
    """Persisted output of a single pipeline stage."""

    stage: str
    run_id: str
    output_json: str
    created_at: str  # ISO-8601
    metadata: dict = Field(default_factory=dict)
