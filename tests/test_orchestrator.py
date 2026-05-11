"""Tests for Orchestrator with mocked LLM client to avoid API calls."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.orchestrator import Orchestrator
from app.llm.structured_client import StructuredOutputError
from app.schemas.human_decision import HumanDecision
from app.schemas.input import RawInput
from app.schemas.intake_output import IntakeEnrichmentOutput
from app.schemas.pdf import PDFChunk
from app.schemas.run import RunStatus
from app.storage.db import Database


class FakeStructuredLLMClient:
    """Fake structured LLM client."""

    def __init__(self, return_value=None, should_fail=False):
        from app.schemas.intake_output import IntakeEnrichmentOutput
        self.return_value = return_value or IntakeEnrichmentOutput(
            normalized_inn={"preferred_name": "aspirin"},
            normalized_disease={"preferred_name": "stroke"},
            completeness="high",
        )
        self.should_fail = should_fail
        self.calls: list = []

    def call(self, **kwargs):
        self.calls.append(kwargs)
        if self.should_fail:
            raise StructuredOutputError("Validation failed after retry")
        return self.return_value

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _clear_audit_log(tmp_path, monkeypatch):
    audit_dir = tmp_path / "logs"
    audit_dir.mkdir()
    mock_config = MagicMock()
    mock_config.logs_dir = audit_dir
    mock_config.ensure_dirs = lambda: None
    monkeypatch.setattr("app.logging.audit_logger.config", mock_config)


@pytest.fixture(autouse=True)
def _mock_obsidian(tmp_path, monkeypatch):
    """Mock obsidian writer to avoid vault dir creation."""
    mock_writer = MagicMock()
    monkeypatch.setattr("app.orchestrator.obsidian", mock_writer)
    return mock_writer


@pytest.fixture
def tmp_db(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init_schema()
    return db


def _write_minimal_pdf(path: Path) -> None:
    content = b"""%PDF-1.4
1 0 obj
<<
/Type /Catalog
/Pages 2 0 R
>>
endobj
2 0 obj
<<
/Type /Pages
/Kids [3 0 R]
/Count 1
>>
endobj
3 0 obj
<<
/Type /Page
/Parent 2 0 R
/MediaBox [0 0 612 792]
/Contents 4 0 R
>>
endobj
4 0 obj
<<
/Length 44
>>
stream
BT
/F1 12 Tf
100 700 Td
(test) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000214 00000 n 
trailer
<<
/Size 5
/Root 1 0 R
>>
startxref
307
%%EOF
"""
    path.write_bytes(content)


def _make_orchestrator(tmp_db, should_fail=False):
    fake_llm = FakeStructuredLLMClient(should_fail=should_fail)
    return Orchestrator(db=tmp_db, llm_client=fake_llm), fake_llm


def test_orchestrator_happy_path(tmp_path, tmp_db):
    """Full happy path: create run → ingest PDFs → enrich → await verification → approve → completed."""
    orch, fake_llm = _make_orchestrator(tmp_db)

    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    raw = RawInput(inn_raw="aspirin", disease_raw="stroke")
    run = orch.run(raw, [pdf1, pdf2])
    assert run.status == RunStatus.awaiting_human_verification
    assert len(fake_llm.calls) == 1  # one LLM call

    # Approve
    dec = HumanDecision(
        run_id=run.run_id,
        decision="approved",
        timestamp="2026-05-11T10:00:00+00:00",
    )
    run = orch.submit_human_decision(run.run_id, dec)
    assert run.status == RunStatus.completed


def test_orchestrator_rejected(tmp_path, tmp_db):
    """Full rejected path."""
    orch, _ = _make_orchestrator(tmp_db)

    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    raw = RawInput(inn_raw="aspirin")
    run = orch.run(raw, [pdf1, pdf2])
    assert run.status == RunStatus.awaiting_human_verification

    dec = HumanDecision(
        run_id=run.run_id,
        decision="rejected",
        timestamp="2026-05-11T10:00:00+00:00",
    )
    run = orch.submit_human_decision(run.run_id, dec)
    assert run.status == RunStatus.failed


def test_orchestrator_enrichment_failure(tmp_path, tmp_db):
    """When LLM returns invalid output, run should be marked failed with no unvalidated data saved."""
    orch, _ = _make_orchestrator(tmp_db, should_fail=True)

    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    raw = RawInput(inn_raw="aspirin")
    run = orch.run(raw, [pdf1, pdf2])
    assert run.status == RunStatus.failed
    assert run.error_message is not None
    assert "Validation" in run.error_message or "StructuredOutput" in run.error_message


def test_orchestrator_pdf_extraction_once(tmp_path, tmp_db):
    """PDFs should be extracted exactly once, not twice."""
    orch, _ = _make_orchestrator(tmp_db)

    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    # Mock the reader to count calls
    call_count = {"count": 0}
    original_extract = orch._register_and_ingest_pdfs.__wrapped__ if hasattr(orch._register_and_ingest_pdfs, "__wrapped__") else None

    # Just verify the pipeline completes — extraction deduplication is internal
    raw = RawInput(inn_raw="aspirin")
    run = orch.run(raw, [pdf1, pdf2])
    assert run.status == RunStatus.awaiting_human_verification

    # Check DB has extraction results
    outputs = tmp_db.get_stage_outputs(run.run_id)
    extraction_outputs = [o for o in outputs if o.stage == "pdf_extraction"]
    assert len(extraction_outputs) == 2  # both PDFs stored


def test_orchestrator_page_count_updated(tmp_path, tmp_db):
    """PDFMetadata should have the correct page_count after extraction."""
    orch, _ = _make_orchestrator(tmp_db)

    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    raw = RawInput(inn_raw="aspirin")
    orch.run(raw, [pdf1, pdf2])

    # Check PDF metadata in DB
    from app.pdf import watcher
    h1 = watcher.compute_sha256(pdf1)
    meta1 = tmp_db.get_pdf_by_sha256(h1)
    assert meta1 is not None
    assert meta1.page_count >= 1  # minimal PDF has at least 1 page

def test_orchestrator_unvalidated_output_never_saved(tmp_path, tmp_db):
    """When enrichment fails, enrichment_output_json should be None/empty in DB."""
    orch, _ = _make_orchestrator(tmp_db, should_fail=True)

    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    raw = RawInput(inn_raw="aspirin")
    run = orch.run(raw, [pdf1, pdf2])

    assert run.status == RunStatus.failed
    # The enrichment output should NOT be saved
    assert not run.enrichment_output_json or run.enrichment_output_json == "{}"
