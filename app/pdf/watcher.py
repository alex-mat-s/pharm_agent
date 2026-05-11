from __future__ import annotations

import hashlib
from pathlib import Path

from app.schemas.pdf import PDFVersionStatus


def compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def check_pdf_status(
    pdf_id: str,
    current_hash: str,
    db_sha256: str | None,
) -> PDFVersionStatus:
    """Compare current file hash with stored DB hash."""
    if db_sha256 is None:
        return PDFVersionStatus.new
    if current_hash == db_sha256:
        return PDFVersionStatus.unchanged
    return PDFVersionStatus.updated
