from pathlib import Path

import pytest

from app.pdf.reader import extract_text_from_pdf
from app.pdf.watcher import compute_sha256, check_pdf_status
from app.schemas.pdf import PDFVersionStatus


def test_compute_sha256_consistent(tmp_path: Path) -> None:
    p = tmp_path / "sample.pdf"
    p.write_bytes(b"hello")
    h1 = compute_sha256(p)
    h2 = compute_sha256(p)
    assert h1 == h2
    assert len(h1) == 64


def test_check_pdf_status_new():
    assert check_pdf_status("a", "abc", None) == PDFVersionStatus.new


def test_check_pdf_status_unchanged():
    assert check_pdf_status("a", "abc", "abc") == PDFVersionStatus.unchanged


def test_check_pdf_status_updated():
    assert check_pdf_status("a", "abc", "def") == PDFVersionStatus.updated


def test_extract_text_from_nonexistent():
    with pytest.raises(Exception):
        extract_text_from_pdf(Path("/nonexistent/file.pdf"), "x")
