"""Tests for SQLite storage layer: runs, steps, PDFs, human decisions, structured outputs, audit events."""
from __future__ import annotations

import pytest

from app.schemas.human_decision import HumanDecision
from app.schemas.input import RawInput
from app.schemas.intake_output import IntakeEnrichmentOutput
from app.schemas.pdf import PDFMetadata
from app.schemas.run import RunStatus
from app.schemas.audit import AuditEvent
from app.storage.db import Database


@pytest.fixture
def tmp_db(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init_schema()
    return db


# =====================================================================
# Runs
# =====================================================================


def test_create_run(tmp_db):
    raw = RawInput(inn_raw="aspirin", disease_raw="stroke")
    run = tmp_db.create_run(raw)
    assert run.status == RunStatus.created
    assert run.run_id.startswith("run_")


def test_update_status_valid(tmp_db):
    run = tmp_db.create_run(RawInput(inn_raw="aspirin"))
    run = tmp_db.update_run_status(run.run_id, RunStatus.input_collected)
    assert run.status == RunStatus.input_collected


def test_update_status_invalid(tmp_db):
    run = tmp_db.create_run(RawInput(inn_raw="aspirin"))
    with pytest.raises(ValueError):
        tmp_db.update_run_status(run.run_id, RunStatus.completed)


# =====================================================================
# Run steps
# =====================================================================


def test_run_steps(tmp_db):
    run = tmp_db.create_run(RawInput(inn_raw="aspirin"))
    tmp_db.upsert_run_step(run.run_id, "pdf_extraction", "started")
    steps = tmp_db.get_run_steps(run.run_id)
    assert len(steps) == 1
    assert steps[0]["step_name"] == "pdf_extraction"
    assert steps[0]["status"] == "started"

    # Update
    tmp_db.upsert_run_step(run.run_id, "pdf_extraction", "completed", details={"pages": 42})
    steps = tmp_db.get_run_steps(run.run_id)
    assert steps[0]["status"] == "completed"


# =====================================================================
# Enrichment output & structured_outputs table
# =====================================================================


def test_save_enrichment_output(tmp_db):
    run = tmp_db.create_run(RawInput(inn_raw="aspirin"))
    out = IntakeEnrichmentOutput(
        normalized_inn={"preferred_name": "Aspirin"},
        completeness="medium",
    )
    tmp_db.save_enrichment_output(run.run_id, out)
    fetched = tmp_db.get_run(run.run_id)
    assert fetched is not None
    assert fetched.enrichment_output_json is not None

    # Also check structured_outputs table
    # (we can inspect via stage_outputs since save_enrichment_output mirrors there too)


# =====================================================================
# Human decisions
# =====================================================================


def test_save_human_decision(tmp_db):
    run = tmp_db.create_run(RawInput(inn_raw="aspirin"))
    dec = HumanDecision(
        run_id=run.run_id,
        decision="approved",
        corrections={"inn_raw": "acetylsalicylic acid"},
        comments="Looks good",
        reviewer_name="Alice",
        timestamp="2026-05-11T10:00:00+00:00",
    )
    tmp_db.save_human_decision(run.run_id, dec)

    # Via dedicated table
    fetched = tmp_db.get_human_decision(run.run_id)
    assert fetched is not None
    assert fetched.decision == "approved"
    assert fetched.corrections["inn_raw"] == "acetylsalicylic acid"
    assert fetched.reviewer_name == "Alice"


def test_save_human_decision_overwrite(tmp_db):
    run = tmp_db.create_run(RawInput(inn_raw="aspirin"))
    dec1 = HumanDecision(
        run_id=run.run_id,
        decision="needs_revision",
        timestamp="2026-05-11T10:00:00+00:00",
    )
    tmp_db.save_human_decision(run.run_id, dec1)
    dec2 = HumanDecision(
        run_id=run.run_id,
        decision="approved",
        timestamp="2026-05-11T11:00:00+00:00",
    )
    tmp_db.save_human_decision(run.run_id, dec2)
    fetched = tmp_db.get_human_decision(run.run_id)
    assert fetched.decision == "approved"


# =====================================================================
# PDFs
# =====================================================================


def test_pdf_register_and_fetch(tmp_db):
    meta = PDFMetadata(
        pdf_id="source_1",
        filename="test.pdf",
        sha256="abc123",
        size_bytes=1024,
        page_count=10,
        modified_timestamp="2026-05-11T10:00:00+00:00",
        ingested_at="2026-05-11T10:00:00+00:00",
        last_seen_at="2026-05-11T10:00:00+00:00",
    )
    tmp_db.register_pdf(meta)
    fetched = tmp_db.get_pdf_by_sha256("abc123")
    assert fetched is not None
    assert fetched.pdf_id == "source_1"
    assert fetched.page_count == 10


def test_pdf_version_tracking(tmp_db):
    run = tmp_db.create_run(RawInput(inn_raw="aspirin"))
    meta = PDFMetadata(
        pdf_id="source_1",
        filename="test.pdf",
        sha256="abc123",
        size_bytes=1024,
        page_count=10,
        modified_timestamp="2026-05-11T10:00:00+00:00",
        ingested_at="2026-05-11T10:00:00+00:00",
        last_seen_at="2026-05-11T10:00:00+00:00",
    )
    tmp_db.register_pdf(meta)
    tmp_db.register_pdf_version(run.run_id, "source_1", "abc123", "new")

    versions = tmp_db.get_pdf_versions_for_run(run.run_id)
    assert len(versions) == 1
    assert versions[0]["sha256"] == "abc123"
    assert versions[0]["version_label"] == "new"


def test_pdf_register_updates_last_seen(tmp_db):
    meta = PDFMetadata(
        pdf_id="source_1",
        filename="test.pdf",
        sha256="abc123",
        size_bytes=1024,
        page_count=10,
        modified_timestamp="2026-05-11T10:00:00+00:00",
        ingested_at="2026-05-11T10:00:00+00:00",
        last_seen_at="2026-05-11T10:00:00+00:00",
    )
    tmp_db.register_pdf(meta)

    meta2 = PDFMetadata(
        pdf_id="source_1",
        filename="test.pdf",
        sha256="abc123",
        size_bytes=2048,
        page_count=12,
        modified_timestamp="2026-05-12T10:00:00+00:00",
        ingested_at="2026-05-11T10:00:00+00:00",
        last_seen_at="2026-05-12T10:00:00+00:00",
    )
    tmp_db.register_pdf(meta2)

    fetched = tmp_db.get_pdf_by_sha256("abc123")
    assert fetched is not None
    assert fetched.size_bytes == 2048
    assert fetched.page_count == 12


# =====================================================================
# Audit events (SQLite mirror)
# =====================================================================


def test_save_audit_event(tmp_db):
    run = tmp_db.create_run(RawInput(inn_raw="aspirin"))
    event = AuditEvent(
        event_id="evt_001",
        run_id=run.run_id,
        stage="intake_enrichment",
        event_type="llm_call",
        timestamp="2026-05-11T10:00:00+00:00",
        status="succeeded",
        metadata={"model": "gpt-4o-mini", "latency_ms": 1200},
    )
    tmp_db.save_audit_event(event)
    events = tmp_db.get_audit_events(run.run_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "llm_call"
    parsed_meta = {"model": "gpt-4o-mini", "latency_ms": 1200}
    assert events[0]["metadata"] == parsed_meta


def test_audit_events_multiple(tmp_db):
    run = tmp_db.create_run(RawInput(inn_raw="aspirin"))
    for i, status in enumerate(["started", "succeeded"]):
        tmp_db.save_audit_event(
            AuditEvent(
                event_id=f"evt_{i}",
                run_id=run.run_id,
                stage="pdf_register",
                event_type="tool_call",
                timestamp=f"2026-05-11T10:0{i}:00+00:00",
                status=status,  # type: ignore[arg-type]
            )
        )
    events = tmp_db.get_audit_events(run.run_id)
    assert len(events) == 2
    assert events[0]["status"] == "started"
    assert events[1]["status"] == "succeeded"
