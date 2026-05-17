from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import config
from app.schemas.human_decision import HumanDecision
from app.schemas.input import NormalizedDisease, NormalizedINN
from app.schemas.pdf import PDFExtractionResult
from app.schemas.run import RunRecord

DISCLAIMER = (
    "This analysis is for R&D and investment research only. "
    "It is not medical advice, clinical guidance, or a substitute "
    "for qualified professional review."
)

VAULT_SUBDIRS = [
    "00_inputs",
    "01_entities/drugs",
    "01_entities/diseases",
    "02_sources/pdfs",
    "03_runs",
    "05_decisions",
    "99_templates",
]

AUTO_BEGIN = "<!-- BEGIN AUTO-GENERATED -->"
AUTO_END = "<!-- END AUTO-GENERATED -->"


def ensure_vault_structure(vault_dir: Path | None = None) -> Path:
    """Create all required vault subdirectories."""
    vd = vault_dir or config.vault_dir
    for sub in VAULT_SUBDIRS:
        (vd / sub).mkdir(parents=True, exist_ok=True)
    return vd


def slugify(name: str, fallback: str = "unknown") -> str:
    """Convert a string to an ASCII-safe slugified filename.

    Falls back to *fallback* when the input contains no Latin alphanumeric
    characters (e.g. pure Cyrillic names).
    """
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower().strip()).strip("-")
    if not slug:
        slug = re.sub(r"[^a-z0-9-]+", "-", fallback.lower().strip()).strip("-")
    return slug or "unknown"


def _frontmatter(data: dict[str, Any]) -> str:
    """Generate YAML frontmatter block."""
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for v in value:
                lines.append(f"  - {v}")
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            for k2, v2 in value.items():
                lines.append(f"  {k2}: {v2}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _write_with_manual_preservation(path: Path, lines: list[str]) -> None:
    """Write auto-generated content while preserving any manual sections below the marker."""
    new_content = "\n".join(lines)

    if path.exists():
        old = path.read_text(encoding="utf-8")
        # Preserve anything the user wrote after END AUTO-GENERATED
        if AUTO_END in old:
            manual_part = old.split(AUTO_END, 1)[1]
            new_content = new_content + manual_part

    path.write_text(new_content, encoding="utf-8")


def write_run_note(record: RunRecord, vault_dir: Path | None = None) -> Path:
    """Create or update a run note in the Obsidian vault."""
    vd = ensure_vault_structure(vault_dir)
    run_dir = vd / "03_runs"
    filename = f"{record.run_id}.md"
    path = run_dir / filename

    front = {
        "type": "analysis_run",
        "run_id": record.run_id,
        "status": record.status.value,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if record.input_hash:
        front["input_hash"] = record.input_hash

    lines = [
        _frontmatter(front),
        "",
        f"# Run {record.run_id}",
        "",
        AUTO_BEGIN,
        "",
        "## Input",
        "",
        "```json",
        f"{record.raw_input_json}",
        "```",
        "",
        "## Status",
        f"Current status: **{record.status.value}**",
        "",
    ]
    if record.enrichment_output_json:
        lines += [
            "## Intake enrichment",
            "",
            "```json",
            f"{record.enrichment_output_json}",
            "```",
            "",
        ]
    if record.final_summary_json:
        lines += [
            "## Final MVP 1 Summary",
            "",
            "```json",
            f"{record.final_summary_json}",
            "```",
            "",
        ]
    if record.error_message:
        lines += [
            "## Error",
            f"{record.error_message}",
            "",
        ]
    lines += [
        "## Disclaimer",
        "",
        f"> {DISCLAIMER}",
        "",
        AUTO_END,
        "",
    ]

    _write_with_manual_preservation(path, lines)
    return path


def write_pdf_source_note(
    result: PDFExtractionResult,
    vault_dir: Path | None = None,
    status: str = "new",
) -> Path:
    """Create or update a PDF source note in the Obsidian vault."""
    vd = ensure_vault_structure(vault_dir)
    pdf_dir = vd / "02_sources" / "pdfs"
    filename = f"{result.pdf_id}.md"
    path = pdf_dir / filename

    front = {
        "type": "pdf_source",
        "pdf_id": result.pdf_id,
        "sha256": result.sha256,
        "page_count": result.page_count,
        "status": status,
        "last_seen_at": _now_iso(),
    }

    lines = [
        _frontmatter(front),
        "",
        f"# {result.pdf_id}",
        "",
        AUTO_BEGIN,
        "",
        "## Extraction status",
        f"Pages extracted: {result.page_count}",
        "",
        "## Page / chunk summary",
        "",
    ]
    for chunk in result.chunks:
        lines.append(f"### Page {chunk.page_number}")
        lines.append(f"Characters: {chunk.char_count}")
        preview = chunk.text[:200].replace("\n", " ")
        lines.append(f"Preview: {preview}...")
        lines.append("")

    lines += [AUTO_END, ""]

    _write_with_manual_preservation(path, lines)
    return path


def write_drug_entity_note(
    inn: NormalizedINN,
    run_id: str,
    vault_dir: Path | None = None,
) -> Path:
    """Create or update a drug entity note."""
    vd = ensure_vault_structure(vault_dir)
    drug_dir = vd / "01_entities" / "drugs"
    slug = slugify(
        inn.preferred_name,
        fallback=inn.english_inn or inn.russian_name or "unknown-drug",
    )
    filename = f"{slug}.md"
    path = drug_dir / filename

    front = {
        "type": "drug",
        "preferred_name": inn.preferred_name,
        "inn_ru": inn.russian_name,
        "inn_en": inn.english_inn,
        "synonyms": inn.synonyms,
        "molecule_type": inn.molecule_type,
        "last_updated": _now_iso(),
        "source_runs": [run_id],
    }

    lines = [
        _frontmatter(front),
        "",
        f"# {inn.preferred_name}",
        "",
        AUTO_BEGIN,
        "",
        "## Identity",
        "",
        f"- INN (EN): {inn.english_inn or 'N/A'}",
        f"- INN (RU): {inn.russian_name or 'N/A'}",
        f"- CAS: {inn.cas or 'N/A'}",
        f"- ATC: {', '.join(inn.atc_codes) or 'N/A'}",
        f"- Molecule type: {inn.molecule_type}",
        "",
        "## Known synonyms",
        "",
        *(f"- {s}" for s in inn.synonyms),
        "",
        AUTO_END,
        "",
        "## MVP 1 notes",
        "",
        "(Human-verified placeholder. Add free-form notes here.)",
        "",
        "## Linked runs",
        f"- [[{run_id}]]",
        "",
    ]

    _write_with_manual_preservation(path, lines)
    return path


def write_disease_entity_note(
    disease: NormalizedDisease,
    run_id: str,
    vault_dir: Path | None = None,
) -> Path:
    """Create or update a disease entity note."""
    vd = ensure_vault_structure(vault_dir)
    disease_dir = vd / "01_entities" / "diseases"
    slug = slugify(
        disease.preferred_name,
        fallback=next((s for s in disease.synonyms if re.search(r"[a-z]", s, re.I)), "unknown-disease"),
    )
    filename = f"{slug}.md"
    path = disease_dir / filename

    front = {
        "type": "disease",
        "preferred_name": disease.preferred_name,
        "synonyms": disease.synonyms,
        "subtypes": disease.subtypes,
        "last_updated": _now_iso(),
        "source_runs": [run_id],
    }

    lines = [
        _frontmatter(front),
        "",
        f"# {disease.preferred_name}",
        "",
        AUTO_BEGIN,
        "",
        "## Identity",
        "",
        f"- MeSH: {', '.join(disease.mesh) or 'N/A'}",
        f"- ICD: {', '.join(disease.icd_codes) or 'N/A'}",
        f"- Biomarkers: {', '.join(disease.biomarkers) or 'N/A'}",
        "",
        "## Possible subtypes",
        "",
        *(f"- {s}" for s in disease.subtypes),
        "",
        AUTO_END,
        "",
        "## MVP 1 notes",
        "",
        "(Human-verified placeholder. Add free-form notes here.)",
        "",
        "## Linked runs",
        f"- [[{run_id}]]",
        "",
    ]

    _write_with_manual_preservation(path, lines)
    return path


def write_decision_note(
    decision: HumanDecision,
    vault_dir: Path | None = None,
) -> Path:
    """Write a human decision note in the Obsidian vault."""
    vd = ensure_vault_structure(vault_dir)
    decision_dir = vd / "05_decisions"
    filename = f"{decision.run_id}_human_verification.md"
    path = decision_dir / filename

    front = {
        "type": "human_decision",
        "run_id": decision.run_id,
        "decision": decision.decision,
        "reviewer": decision.reviewer_name,
        "timestamp": decision.timestamp,
    }

    lines = [
        _frontmatter(front),
        "",
        f"# Human Verification Decision: {decision.run_id}",
        "",
        f"**Decision:** {decision.decision}",
        f"**Reviewer:** {decision.reviewer_name or 'N/A'}",
        f"**Timestamp:** {decision.timestamp}",
        "",
        "## Corrections",
        "",
    ]
    for field, value in decision.corrections.items():
        lines.append(f"- {field}: {value}")
    if not decision.corrections:
        lines.append("No corrections made.")
    lines += [
        "",
        "## Comments",
        "",
        decision.comments or "No comments provided.",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
