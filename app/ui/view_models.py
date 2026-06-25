"""View models: transform backend data into UI-friendly representations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class IntakeFormData:
    """Validated intake form data ready for the orchestrator."""

    inn_raw: str
    disease_raw: str | None
    region: str | None
    molecule_type: str | None
    stage: str | None
    pdf_paths: list[Path]

    def validation_summary(self) -> str:
        """Human-readable pre-submit summary."""
        lines = [
            "── Pre-submit Validation ──",
            f"  INN: {self.inn_raw}",
        ]
        if self.disease_raw:
            lines.append(f"  Disease: {self.disease_raw}")
        if self.region:
            lines.append(f"  Region: {self.region}")
        if self.molecule_type:
            lines.append(f"  Molecule type: {self.molecule_type}")
        if self.stage:
            lines.append(f"  Stage: {self.stage}")
        lines.append(f"  PDF files ({len(self.pdf_paths)}):")
        for p in self.pdf_paths:
            lines.append(f"    • {p.name}")
        lines.append("")
        lines.append("⏭ After submission, the system will run enrichment and require your verification.")
        return "\n".join(lines)


@dataclass
class IntakeValidationError:
    """Structured form validation error."""

    field: str
    message: str


def validate_intake_form(
    inn: str | None,
    disease: str | None,
    region: str | None,
    molecule_type: str | None,
    stage: str | None,
    pdf_files: list | None,
) -> tuple[IntakeFormData | None, list[IntakeValidationError]]:
    """Validate intake form inputs, return form data or errors."""
    errors: list[IntakeValidationError] = []

    inn_clean = (inn or "").strip()
    if not inn_clean:
        errors.append(IntakeValidationError("inn", "INN is a required field."))

    pdf_paths: list[Path] = []
    if not pdf_files or len(pdf_files) < 2:
        errors.append(IntakeValidationError(
            "pdf",
            f"You must upload exactly 2 PDF files (uploaded: {len(pdf_files) if pdf_files else 0}).",
        ))
    elif len(pdf_files) > 2:
        errors.append(IntakeValidationError(
            "pdf",
            f"Upload exactly 2 PDF files, no more (uploaded: {len(pdf_files)}).",
        ))
    else:
        for f in pdf_files:
            p = Path(f) if isinstance(f, str) else Path(f.name) if hasattr(f, "name") else None
            if p is None or not p.exists():
                errors.append(IntakeValidationError("pdf", f"PDF file not found: {f}"))
            else:
                pdf_paths.append(p.resolve())

    if errors:
        return None, errors

    valid_stages = {"idea", "preclinical", "phase1", "phase2", "phase3", "approved", "repurposing"}
    stage_clean = (stage or "").strip() or None
    if stage_clean and stage_clean not in valid_stages:
        errors.append(IntakeValidationError(
            "stage",
            f"Stage must be one of: {', '.join(sorted(valid_stages))}.",
        ))

    if errors:
        return None, errors

    form_data = IntakeFormData(
        inn_raw=inn_clean,
        disease_raw=(disease or "").strip() or None,
        region=(region or "").strip() or None,
        molecule_type=(molecule_type or "").strip() or None,
        stage=stage_clean,
        pdf_paths=pdf_paths,
    )
    return form_data, []


@dataclass
class RunStartResult:
    """Result of starting a run, for display in the UI."""

    success: bool
    run_id: str | None = None
    status: str | None = None
    error: str | None = None
    next_action: str = ""


@dataclass
class DecisionResult:
    """Result of submitting a human decision."""

    success: bool
    run_id: str | None = None
    status: str | None = None
    error: str | None = None
    next_action: str = ""


def format_verification_packet(packet) -> str:  # noqa: ANN001
    """Render HumanVerificationPacket as readable Markdown for the verification screen."""
    lines: list[str] = []

    lines.append(f"**Run ID:** `{packet.run_id}`")
    lines.append(f"**Data completeness:** `{packet.completeness}`")

    if packet.completeness == "low":
        lines.append("\n> ⚠️ **WARNING:** Data completeness is LOW — critical information is missing. "
                     "Consider sending for revision.\n")

    lines.append("\n---\n### Raw Input Data")
    lines.append(f"- **INN:** {packet.raw_inn}")
    lines.append(f"- **Disease:** {packet.raw_disease or '— not specified —'}")

    lines.append("\n### Normalized INN")
    inn = packet.normalized_inn
    inn_data = inn if isinstance(inn, dict) else inn.__dict__ if hasattr(inn, "__dict__") else {}
    if hasattr(inn, "preferred_name"):
        inn_data = {
            "preferred_name": inn.preferred_name,
            "english_inn": inn.english_inn,
            "russian_name": inn.russian_name,
            "synonyms": inn.synonyms,
            "brand_names": inn.brand_names,
            "molecule_type": inn.molecule_type,
            "confidence": inn.confidence,
        }

    if inn_data.get("preferred_name"):
        lines.append(f"- **Preferred name:** {inn_data['preferred_name']}")
        if inn_data.get("english_inn"):
            lines.append(f"- **English INN:** {inn_data['english_inn']}")
        if inn_data.get("russian_name"):
            lines.append(f"- **Russian name:** {inn_data['russian_name']}")
        if inn_data.get("synonyms"):
            lines.append(f"- **Synonyms:** {', '.join(inn_data['synonyms'])}")
        if inn_data.get("brand_names"):
            lines.append(f"- **Brands:** {', '.join(inn_data['brand_names'])}")
        if inn_data.get("molecule_type"):
            lines.append(f"- **Molecule type:** {inn_data['molecule_type']}")
        if inn_data.get("confidence"):
            lines.append(f"- **Confidence:** {inn_data['confidence']}")
    else:
        lines.append(f"  {inn}")

    if packet.normalized_disease:
        lines.append("\n### Normalized Disease")
        dis = packet.normalized_disease
        dis_data = dis if isinstance(dis, dict) else {}
        if hasattr(dis, "preferred_name"):
            dis_data = {
                "preferred_name": dis.preferred_name,
                "synonyms": dis.synonyms,
                "subtypes": dis.subtypes,
                "confidence": dis.confidence,
            }

        if dis_data.get("preferred_name"):
            lines.append(f"- **Preferred name:** {dis_data['preferred_name']}")
            if dis_data.get("synonyms"):
                lines.append(f"- **Synonyms:** {', '.join(dis_data['synonyms'])}")
            if dis_data.get("subtypes"):
                lines.append(f"- **Subtypes:** {', '.join(dis_data['subtypes'])}")
            if dis_data.get("confidence"):
                lines.append(f"- **Confidence:** {dis_data['confidence']}")
        else:
            lines.append(f"  {dis}")

    if packet.ambiguities:
        lines.append("\n### Ambiguities")
        for a in packet.ambiguities:
            lines.append(f"- {a}")

    if packet.assumptions:
        lines.append("\n### Assumptions")
        for a in packet.assumptions:
            lines.append(f"- {a}")

    if packet.missing_information:
        lines.append("\n### Missing Information")
        for m in packet.missing_information:
            lines.append(f"- {m}")

    if packet.questions:
        lines.append("\n### Questions for Reviewer")
        for q in packet.questions:
            lines.append(f"- ❓ {q}")

    if packet.pdf_extraction_status:
        lines.append("\n### PDF Status")
        for pid, st in packet.pdf_extraction_status.items():
            lines.append(f"- `{pid}`: {st}")

    return "\n".join(lines)
