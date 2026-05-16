"""Tests for CLI with mocked Orchestrator/LLM."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.orchestrator import Orchestrator
from app.schemas.human_decision import HumanDecision
from app.schemas.run import RunStatus

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_audit_log(tmp_path, monkeypatch):
    audit_dir = tmp_path / "logs"
    audit_dir.mkdir()
    mock_config = MagicMock()
    mock_config.logs_dir = audit_dir
    mock_config.ensure_dirs = lambda: None
    monkeypatch.setattr("app.logging.audit_logger.config", mock_config)


@pytest.fixture
def _mock_obsidian(tmp_path, monkeypatch):
    """Mock obsidian writer."""
    mock_writer = MagicMock()
    monkeypatch.setattr("app.orchestrator.obsidian", mock_writer)
    return mock_writer


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


class FakeLLMClient:
    """Fake LLM client that returns valid IntakeEnrichmentOutput."""
    def __init__(self):
        from app.schemas.intake_output import IntakeEnrichmentOutput
        self.return_value = IntakeEnrichmentOutput(
            normalized_inn={"preferred_name": "aspirin"},
            normalized_disease={"preferred_name": "stroke"},
            completeness="high",
        )

    def call(self, **kwargs) -> Any:
        return self.return_value

    def close(self):
        pass


def test_cli_run_missing_pdf():
    result = runner.invoke(
        app,
        ["run", "--inn", "aspirin", "--pdf1", "/nonexistent/a.pdf", "--pdf2", "/nonexistent/b.pdf"],
    )
    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "error" in result.output.lower()


def test_cli_run_and_verify(tmp_path, _mock_obsidian):
    """Full CLI happy path with mocked LLM client."""
    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf(pdf2)

    # Patch Orchestrator to use fake LLM client
    fake_llm = FakeLLMClient()

    with patch("app.cli.Orchestrator") as MockOrchestrator:
        # Use a fresh temp database so stale schema never interferes
        from app.storage.db import Database
        db_path = tmp_path / "cli_test.sqlite"

        def _mock_constructor(*, db):
            test_db = Database(db_path)
            test_db.init_schema()
            return Orchestrator(db=test_db, llm_client=fake_llm)

        MockOrchestrator.side_effect = _mock_constructor

        result = runner.invoke(
            app,
            [
                "run",
                "--inn",
                "aspirin",
                "--disease",
                "stroke",
                "--pdf1",
                str(pdf1),
                "--pdf2",
                str(pdf2),
            ],
        )

    assert result.exit_code == 0, f"CLI run failed: {result.output}"
    assert "Run created:" in result.output
    run_id = None
    for line in result.output.splitlines():
        if "Run created:" in line:
            run_id = line.split("Run created:")[1].strip()
            break
    assert run_id is not None

    # Also mock for verify
    with patch("app.cli.Orchestrator") as MockOrchestrator:
        MockOrchestrator.side_effect = _mock_constructor
        result2 = runner.invoke(
            app,
            ["verify", "--run-id", run_id, "--decision", "approved"],
        )

    assert result2.exit_code == 0, f"CLI verify failed: {result2.output}"
    assert "completed" in result2.output


def test_cli_verify_bad_decision():
    result = runner.invoke(
        app,
        ["verify", "--run-id", "run_x", "--decision", "maybe"],
    )
    assert result.exit_code != 0
    assert "approved, rejected, or needs_revision" in result.output


def test_cli_status_missing_run():
    result = runner.invoke(app, ["status", "--run-id", "run_does_not_exist"])
    assert result.exit_code != 0
    assert "not found" in result.output
