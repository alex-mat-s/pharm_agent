from pathlib import Path

import pytest

from app.schemas.human_decision import HumanDecision
from app.schemas.input import RawInput
from app.schemas.intake_output import IntakeEnrichmentOutput
from app.schemas.pdf import PDFMetadata
from app.schemas.run import RunStatus
from app.storage.db import Database


def test_create_run(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite")
    db.init_schema()
    raw = RawInput(inn_raw="aspirin", disease_raw="stroke")
    run = db.create_run(raw)
    assert run.status == RunStatus.created
    assert run.run_id.startswith("run_")


def test_update_status_valid(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite")
    db.init_schema()
    run = db.create_run(RawInput(inn_raw="aspirin"))
    run = db.update_run_status(run.run_id, RunStatus.input_collected)
    assert run.status == RunStatus.input_collected


def test_update_status_invalid(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite")
    db.init_schema()
    run = db.create_run(RawInput(inn_raw="aspirin"))
    with pytest.raises(ValueError):
        db.update_run_status(run.run_id, RunStatus.completed)


def test_save_enrichment_output(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite")
    db.init_schema()
    run = db.create_run(RawInput(inn_raw="aspirin"))
    out = IntakeEnrichmentOutput(
        normalized_inn={"preferred_name": "Aspirin"},
        completeness="medium",
    )
    db.save_enrichment_output(run.run_id, out)
    fetched = db.get_run(run.run_id)
    assert fetched is not None
    assert fetched.enrichment_output_json is not None


def test_save_human_decision(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite")
    db.init_schema()
    run = db.create_run(RawInput(inn_raw="aspirin"))
    dec = HumanDecision(
        run_id=run.run_id,
        decision="approved",
        timestamp="2026-05-11T10:00:00+00:00",
    )
    db.save_human_decision(run.run_id, dec)
    fetched = db.get_run(run.run_id)
    assert fetched is not None
    assert fetched.human_decision_json is not None


def test_pdf_register_and_fetch(tmp_path: Path):
    db = Database(tmp_path / "test.sqlite")
    db.init_schema()
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
    db.register_pdf(meta)
    fetched = db.get_pdf_by_sha256("abc123")
    assert fetched is not None
    assert fetched.pdf_id == "source_1"
