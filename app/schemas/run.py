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
    # MVP2 scientific stage
    scientific_evidence_collected = "scientific_evidence_collected"
    scientific_analyzed = "scientific_analyzed"
    # MVP3 market stage
    market_analyzed = "market_analyzed"
    completed = "completed"
    failed = "failed"


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
    RunStatus.human_approved: [
        RunStatus.scientific_evidence_collected,
        RunStatus.completed,
        RunStatus.failed,
    ],
    # Rejection is a first-class business outcome, not an internal failure.
    RunStatus.human_rejected: [RunStatus.completed, RunStatus.failed],
    RunStatus.needs_revision: [RunStatus.input_collected],
    # MVP2 scientific stage transitions
    RunStatus.scientific_evidence_collected: [
        RunStatus.scientific_analyzed,
        RunStatus.failed,
    ],
    RunStatus.scientific_analyzed: [RunStatus.market_analyzed, RunStatus.completed, RunStatus.failed],
    RunStatus.market_analyzed: [RunStatus.completed, RunStatus.failed],
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
    input_hash: str | None = None
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


class MVP1Summary(BaseModel):
    """Final summary produced after human approval for MVP 1."""

    run_id: str
    inn_preferred: str
    inn_english: str | None = None
    inn_russian: str | None = None
    disease_preferred: str | None = None
    input_hash: str
    pdf_hashes: dict[str, str] = Field(default_factory=dict)
    enrichment_completeness: str = "medium"
    human_decision: str = "approved"
    disclaimer: str = (
        "This analysis is for R&D and investment research only. "
        "It is not medical advice, clinical guidance, or a substitute "
        "for qualified professional review."
    )
