"""Tests for Obsidian writer, including scientific memo generation."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.obsidian.writer import (
    ensure_vault_structure,
    slugify,
    write_scientific_memo,
    write_run_note,
)
from app.schemas.evidence import SourceRecord, SourceType
from app.schemas.run import RunRecord, RunStatus
from app.schemas.scientific import ScientificAgentOutput, SourceClaim, ApprovedTherapy


@pytest.fixture
def vault_dir(tmp_path):
    return tmp_path / "vault"


@pytest.fixture(autouse=True)
def _patch_config(vault_dir, monkeypatch):
    mock_config = MagicMock()
    mock_config.vault_dir = vault_dir
    monkeypatch.setattr("app.obsidian.writer.config", mock_config)


def test_ensure_vault_structure_creates_dirs(vault_dir):
    result = ensure_vault_structure(vault_dir)
    assert (vault_dir / "04_reports").exists()
    assert (vault_dir / "02_sources" / "pubmed").exists()
    assert (vault_dir / "02_sources" / "clinicaltrials").exists()


def test_slugify():
    assert slugify("Acetylsalicylic Acid") == "acetylsalicylic-acid"
    assert slugify("") == "unknown"
    assert slugify("123-test") == "123-test"


def test_write_scientific_memo_creates_file(vault_dir):
    output = ScientificAgentOutput(
        executive_summary="Aspirin shows promise for stroke prevention.",
        mechanism_of_action=SourceClaim(claim="COX-1 inhibition", source_ids=["pubmed:123"]),
        approved_therapies=[ApprovedTherapy(name="Aspirin", regulatory_status="Approved", source_ids=["fda:NDA001"])],
        evidence_gaps=["No long-term safety data"],
        confidence="medium",
    )
    sources = [
        SourceRecord(
            source_id="pubmed:123",
            source_type=SourceType.pubmed,
            title="Aspirin Study",
            retrieved_at="2026-01-01T00:00:00+00:00",
            query_used="aspirin",
            citation_label="Smith A. Aspirin Study. 2024.",
        ),
    ]

    path = write_scientific_memo(
        run_id="run_test_001",
        output=output,
        sources=sources,
        coverage={"pubmed": "ok (1 results)"},
        pdf_hashes={"source_1": "abc123"},
        vault_dir=vault_dir,
    )

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Scientific Memo" in content
    assert "Aspirin shows promise" in content
    assert "COX-1 inhibition" in content
    assert "pubmed:123" in content
    assert "No long-term safety data" in content
    assert "Disclaimer" in content
    assert "run_test_001" in content


def test_scientific_memo_preserves_manual_sections(vault_dir):
    output = ScientificAgentOutput(executive_summary="First version")
    path = write_scientific_memo(
        run_id="run_test_002",
        output=output,
        vault_dir=vault_dir,
    )

    content = path.read_text(encoding="utf-8")
    path.write_text(content + "\n## My Manual Notes\n\nKeep this!\n", encoding="utf-8")

    output2 = ScientificAgentOutput(executive_summary="Updated version")
    path2 = write_scientific_memo(
        run_id="run_test_002",
        output=output2,
        vault_dir=vault_dir,
    )

    updated = path2.read_text(encoding="utf-8")
    assert "Updated version" in updated
    assert "Keep this!" in updated


def test_write_run_note(vault_dir):
    record = RunRecord(
        run_id="run_test_003",
        status=RunStatus.completed,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        raw_input_json='{"inn_raw": "aspirin"}',
    )
    path = write_run_note(record, vault_dir=vault_dir)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "run_test_003" in content
    assert "completed" in content


def test_scientific_memo_with_empty_output(vault_dir):
    output = ScientificAgentOutput()
    path = write_scientific_memo(
        run_id="run_empty",
        output=output,
        vault_dir=vault_dir,
    )
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Not assessed" in content or "No executive summary" in content
