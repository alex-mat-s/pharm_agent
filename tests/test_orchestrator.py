"""Tests for Orchestrator with mocked LLM client to avoid API calls."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.llm.structured_client import StructuredOutputError
from app.orchestrator import Orchestrator, compute_input_hash
from app.schemas.human_decision import HumanDecision
from app.schemas.input import RawInput
from app.schemas.intake_output import IntakeEnrichmentOutput
from app.schemas.evidence import ConnectorQuery, ConnectorResult
from app.schemas.run import RunStatus
from app.schemas.scientific import ScientificAgentOutput
from app.storage.db import Database


class FakeStructuredLLMClient:
    """Fake structured LLM client that returns different outputs per output_model."""

    def __init__(self, return_value=None, should_fail=False):
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
        output_model = kwargs.get("output_model")
        if output_model is ScientificAgentOutput:
            return ScientificAgentOutput(
                executive_summary="Aspirin is well-studied for stroke.",
                evidence_gaps=["No novel data"],
                source_ids_used=["pubmed:12345"],
                confidence="medium",
            )
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
    mock_writer.write_scientific_memo.return_value = tmp_path / "memo.md"
    (tmp_path / "memo.md").write_text("# Test memo", encoding="utf-8")
    monkeypatch.setattr("app.orchestrator.obsidian", mock_writer)
    return mock_writer


def _empty_connector_result(name: str) -> ConnectorResult:
    return ConnectorResult(
        connector_name=name,
        query=ConnectorQuery(inn="aspirin"),
    )


@pytest.fixture(autouse=True)
def _mock_connectors(monkeypatch):
    """Mock all external connectors to return empty results."""
    for cls_path in [
        "app.orchestrator.PubMedConnector",
        "app.orchestrator.ClinicalTrialsConnector",
        "app.orchestrator.EMAConnector",
    ]:
        name = cls_path.split(".")[-1].replace("Connector", "").lower()
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.search.return_value = _empty_connector_result(name)
        mock_cls.return_value = mock_instance
        mock_cls.connector_name = name
        monkeypatch.setattr(cls_path, mock_cls)


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


# =====================================================================
# Legacy .run() + .submit_human_decision() tests (backward compat)
# =====================================================================


def test_orchestrator_happy_path(tmp_path, tmp_db):
    """Full happy path: create → enrich → verify → approve → scientific → completed."""
    orch, fake_llm = _make_orchestrator(tmp_db)

    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    raw = RawInput(inn_raw="aspirin", disease_raw="stroke")
    run = orch.run(raw, [pdf1, pdf2])
    assert run.status == RunStatus.awaiting_human_verification
    assert len(fake_llm.calls) == 1

    dec = HumanDecision(
        run_id=run.run_id,
        decision="approved",
        timestamp="2026-05-11T10:00:00+00:00",
    )
    run = orch.submit_human_decision(run.run_id, dec)
    assert run.status == RunStatus.completed
    # MVP2: scientific agent was called after approval
    assert len(fake_llm.calls) == 2


def test_orchestrator_rejected(tmp_path, tmp_db):
    """Rejected path ends with completed (not failed)."""
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
    assert run.status == RunStatus.completed


def test_orchestrator_enrichment_failure(tmp_path, tmp_db):
    """When LLM returns invalid output, run should be marked failed."""
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
    """PDFs should be extracted exactly once."""
    orch, _ = _make_orchestrator(tmp_db)

    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    raw = RawInput(inn_raw="aspirin")
    run = orch.run(raw, [pdf1, pdf2])
    assert run.status == RunStatus.awaiting_human_verification

    outputs = tmp_db.get_stage_outputs(run.run_id)
    extraction_outputs = [o for o in outputs if o.stage == "pdf_extraction"]
    assert len(extraction_outputs) == 2


def test_orchestrator_page_count_updated(tmp_path, tmp_db):
    """PDFMetadata should have the correct page_count after extraction."""
    orch, _ = _make_orchestrator(tmp_db)

    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    raw = RawInput(inn_raw="aspirin")
    orch.run(raw, [pdf1, pdf2])

    from app.pdf import watcher
    h1 = watcher.compute_sha256(pdf1)
    meta1 = tmp_db.get_pdf_by_sha256(h1)
    assert meta1 is not None
    assert meta1.page_count >= 1


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
    assert not run.enrichment_output_json or run.enrichment_output_json == "{}"


# =====================================================================
# Two-phase flow tests (run_until_verification + finalize_decision)
# =====================================================================


def test_two_phase_approved(tmp_path, tmp_db):
    """Two-phase flow: run_until_verification → finalize_decision(approved) → scientific → completed."""
    orch, fake_llm = _make_orchestrator(tmp_db)

    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    raw = RawInput(inn_raw="aspirin", disease_raw="stroke")
    run, packet = orch.run_until_verification(raw, [pdf1, pdf2])
    assert run.status == RunStatus.awaiting_human_verification
    assert packet is not None
    assert packet.raw_inn == "aspirin"

    dec = HumanDecision(run_id=run.run_id, decision="approved", timestamp="2026-05-17T00:00:00+00:00")
    run, summary = orch.finalize_decision(run.run_id, dec)
    assert run.status == RunStatus.completed
    assert summary is not None
    assert summary.inn_preferred == "aspirin"
    assert summary.human_decision == "approved"
    assert summary.input_hash  # non-empty
    # MVP2: scientific agent was called
    assert len(fake_llm.calls) == 2

    # Scientific output persisted
    sci_output = tmp_db.get_scientific_output(run.run_id)
    assert sci_output is not None


def test_two_phase_rejected(tmp_path, tmp_db):
    """Two-phase flow: rejected run completes with no summary."""
    orch, _ = _make_orchestrator(tmp_db)

    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    raw = RawInput(inn_raw="aspirin")
    run, packet = orch.run_until_verification(raw, [pdf1, pdf2])
    assert run.status == RunStatus.awaiting_human_verification

    dec = HumanDecision(run_id=run.run_id, decision="rejected", timestamp="2026-05-17T00:00:00+00:00")
    run, summary = orch.finalize_decision(run.run_id, dec)
    assert run.status == RunStatus.completed
    assert summary is None


def test_input_hash_deterministic():
    """Same input should produce the same hash."""
    raw = RawInput(inn_raw="aspirin", disease_raw="stroke")
    h1 = compute_input_hash(raw)
    h2 = compute_input_hash(raw)
    assert h1 == h2
    assert len(h1) == 64


def test_input_hash_stored_in_db(tmp_path, tmp_db):
    """Input hash should be persisted in the runs table."""
    orch, _ = _make_orchestrator(tmp_db)

    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    raw = RawInput(inn_raw="aspirin")
    run, _ = orch.run_until_verification(raw, [pdf1, pdf2])

    fetched = tmp_db.get_run(run.run_id)
    assert fetched is not None
    assert fetched.input_hash
    assert len(fetched.input_hash) == 64


def test_enrichment_failure_returns_none_packet(tmp_path, tmp_db):
    """When enrichment fails, packet should be None."""
    orch, _ = _make_orchestrator(tmp_db, should_fail=True)

    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    raw = RawInput(inn_raw="aspirin")
    run, packet = orch.run_until_verification(raw, [pdf1, pdf2])
    assert run.status == RunStatus.failed
    assert packet is None
