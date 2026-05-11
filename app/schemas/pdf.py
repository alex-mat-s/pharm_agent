from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class PDFVersionStatus(str, Enum):
    new = "new"
    unchanged = "unchanged"
    updated = "updated"
    missing = "missing"


class PDFMetadata(BaseModel):
    """Metadata for a single PDF source document."""

    pdf_id: str
    filename: str
    sha256: str
    size_bytes: int
    page_count: int
    modified_timestamp: str  # ISO-8601
    ingested_at: str  # ISO-8601
    last_seen_at: str  # ISO-8601


class PDFChunk(BaseModel):
    """A single extracted text chunk from a PDF page."""

    pdf_id: str
    page_number: int
    text: str
    char_count: int


class PDFExtractionResult(BaseModel):
    """Result of PDF text extraction."""

    pdf_id: str
    sha256: str
    page_count: int
    chunks: list[PDFChunk]
