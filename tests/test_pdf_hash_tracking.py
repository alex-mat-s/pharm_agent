"""Tests for PDF hash tracking: unchanged, changed, missing, and non-PDF handling."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.orchestrator import Orchestrator
from app.pdf import watcher
from app.schemas.input import RawInput
from app.storage.db import Database


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


def _write_minimal_pdf_v2(path: Path) -> None:
    """Same structure, different text content → different hash."""
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
(changed) Tj
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
    mock_writer = MagicMock()
    monkeypatch.setattr("app.orchestrator.obsidian", mock_writer)
    return mock_writer


@pytest.fixture
def tmp_db(tmp_path):
    db = Database(tmp_path / "test.sqlite")
    db.init_schema()
    return db


class FakeLLMClient:
    def __init__(self):
        from app.schemas.intake_output import IntakeEnrichmentOutput
        self.return_value = IntakeEnrichmentOutput(
            normalized_inn={"preferred_name": "aspirin"},
            completeness="high",
        )

    def call(self, **kwargs: Any) -> Any:
        return self.return_value

    def close(self) -> None:
        pass


def test_unchanged_pdf_reuses_hash(tmp_path, tmp_db):
    """Same PDFs used again should be detected as unchanged."""
    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf_v2(pdf2)  # Different content → different hash

    orch = Orchestrator(db=tmp_db, llm_client=FakeLLMClient())
    raw = RawInput(inn_raw="aspirin")
    run1 = orch.run(raw, [pdf1, pdf2])

    # Same PDFs again → both should be unchanged
    run2 = orch.run(raw, [pdf1, pdf2])

    v1 = tmp_db.get_pdf_versions_for_run(run1.run_id)
    v2 = tmp_db.get_pdf_versions_for_run(run2.run_id)
    assert all(v["version_label"] == "new" for v in v1)
    assert all(v["version_label"] == "unchanged" for v in v2)


def test_changed_pdf_detected_updated(tmp_path, tmp_db):
    """PDF with same slot but different content → updated."""
    pdf1 = tmp_path / "a.pdf"
    _write_minimal_pdf(pdf1)

    orch = Orchestrator(db=tmp_db, llm_client=FakeLLMClient())
    raw = RawInput(inn_raw="aspirin")
    run1 = orch.run(raw, [pdf1, pdf1])
    v1 = tmp_db.get_pdf_versions_for_run(run1.run_id)
    # Both slots: same file used twice, first slot sets status → both "new"
    assert v1[0]["version_label"] == "new"
    assert v1[1]["version_label"] == "new"

    # Modify PDF content (different hash)
    _write_minimal_pdf_v2(pdf1)
    run2 = orch.run(raw, [pdf1, pdf1])
    v2 = tmp_db.get_pdf_versions_for_run(run2.run_id)
    # Both slots: hash differs from previous source_1 → updated
    assert all(v["version_label"] == "updated" for v in v2)


def test_missing_pdf_raises_error(tmp_path, tmp_db):
    """Nonexistent PDF should raise FileNotFoundError."""
    orch = Orchestrator(db=tmp_db, llm_client=FakeLLMClient())
    raw = RawInput(inn_raw="aspirin")
    with pytest.raises(FileNotFoundError):
        orch.run(raw, [Path("/nonexistent/file.pdf"), Path("/nonexistent/file2.pdf")])


def test_non_pdf_file_still_hashed(tmp_path, tmp_db):
    """A non-PDF file is still hashed; we just verify no crash and hash computed."""
    txt = tmp_path / "not_a_pdf.txt"
    txt.write_text("This is not a PDF.")

    # Hash computation works on any file
    h = watcher.compute_sha256(txt)
    assert len(h) == 64


def test_pdf_hash_computed_consistently(tmp_path):
    """SHA-256 of the same file should be identical."""
    pdf = tmp_path / "a.pdf"
    _write_minimal_pdf(pdf)
    h1 = watcher.compute_sha256(pdf)
    h2 = watcher.compute_sha256(pdf)
    assert h1 == h2
    assert len(h1) == 64


def test_pdf_hash_changes_with_content(tmp_path):
    """Different PDF content should produce different hashes."""
    pdf1 = tmp_path / "a.pdf"
    pdf2 = tmp_path / "b.pdf"
    _write_minimal_pdf(pdf1)
    _write_minimal_pdf_v2(pdf2)
    h1 = watcher.compute_sha256(pdf1)
    h2 = watcher.compute_sha256(pdf2)
    assert h1 != h2
