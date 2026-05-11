from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from app.schemas.pdf import PDFChunk, PDFExtractionResult
from app.pdf.watcher import compute_sha256


def extract_text_from_pdf(pdf_path: Path, pdf_id: str) -> PDFExtractionResult:
    """Extract page-level text from a PDF using PyMuPDF."""
    sha256 = compute_sha256(pdf_path)
    doc = fitz.open(str(pdf_path))
    chunks: list[PDFChunk] = []
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        text = page.get_text()
        chunks.append(
            PDFChunk(
                pdf_id=pdf_id,
                page_number=page_num + 1,
                text=text,
                char_count=len(text),
            )
        )
    doc.close()
    return PDFExtractionResult(
        pdf_id=pdf_id,
        sha256=sha256,
        page_count=len(chunks),
        chunks=chunks,
    )
