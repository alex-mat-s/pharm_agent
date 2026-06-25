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
    "02_sources/pubmed",
    "02_sources/clinicaltrials",
    "02_sources/fda",
    "02_sources/ema",
    "02_sources/orange_book",
    "02_sources/purple_book",
    "02_sources/epo_ops",
    "02_sources/uspto",
    "02_sources/wipo",
    "02_sources/patents/rospatent",
    "02_sources/patents/fips",
    "02_sources/patents/eapo",
    "03_runs",
    "04_reports/scientific",
    "04_reports/market",
    "04_reports/patent_finance",
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


def write_scientific_memo(
    run_id: str,
    output: Any,
    sources: list[Any] | None = None,
    coverage: dict[str, str] | None = None,
    pdf_hashes: dict[str, str] | None = None,
    vault_dir: Path | None = None,
) -> Path:
    """Write a scientific analysis memo to the Obsidian vault reports folder."""
    vd = ensure_vault_structure(vault_dir)
    reports_dir = vd / "04_reports" / "scientific"
    filename = f"{run_id}_scientific_memo.md"
    path = reports_dir / filename

    front: dict[str, Any] = {
        "type": "scientific_memo",
        "run_id": run_id,
        "confidence": getattr(output, "confidence", "medium"),
        "created_at": _now_iso(),
    }
    if pdf_hashes:
        front["pdf_hashes"] = pdf_hashes
    if coverage:
        front["connector_coverage"] = coverage

    lines = [
        _frontmatter(front),
        "",
        f"# Scientific Memo — {run_id}",
        "",
        AUTO_BEGIN,
        "",
        "## Executive Summary",
        "",
        getattr(output, "executive_summary", "") or "(No executive summary)",
        "",
    ]

    def _claim_section(title: str, claim: Any | None) -> list[str]:
        if claim is None:
            return [f"## {title}", "", "(Not assessed)", ""]
        source_refs = ", ".join(getattr(claim, "source_ids", []))
        return [
            f"## {title}",
            "",
            getattr(claim, "claim", str(claim)),
            f"Sources: {source_refs}" if source_refs else "",
            "",
        ]

    lines += _claim_section("Mechanism of Action", getattr(output, "mechanism_of_action", None))
    lines += _claim_section("Disease Pathophysiology", getattr(output, "disease_pathophysiology", None))
    lines += _claim_section("Mechanistic Rationale", getattr(output, "mechanistic_rationale", None))

    existing_evi = getattr(output, "existing_evidence", [])
    lines += ["## Existing Evidence", ""]
    if existing_evi:
        for claim in existing_evi:
            refs = ", ".join(getattr(claim, "source_ids", []))
            lines.append(f"- {getattr(claim, 'claim', str(claim))} [{refs}]")
    else:
        lines.append("(No existing evidence entries)")
    lines.append("")

    lines += _claim_section("Standard of Care", getattr(output, "standard_of_care", None))

    approved = getattr(output, "approved_therapies", [])
    lines += ["## Approved Therapies", ""]
    if approved:
        for t in approved:
            refs = ", ".join(getattr(t, "source_ids", []))
            lines.append(f"- **{getattr(t, 'name', '')}**: {getattr(t, 'regulatory_status', '')} [{refs}]")
    else:
        lines.append("(No approved therapies found)")
    lines.append("")

    trials = getattr(output, "clinical_trial_landscape", [])
    lines += ["## Clinical Trial Landscape", ""]
    if trials:
        for t in trials:
            lines.append(
                f"- **{getattr(t, 'nct_id', '')}**: {getattr(t, 'title', '')} "
                f"(Phase {getattr(t, 'phase', '')}, {getattr(t, 'status', '')})"
            )
    else:
        lines.append("(No clinical trials found)")
    lines.append("")

    safety = getattr(output, "safety_considerations", [])
    lines += ["## Safety and Tolerability", ""]
    if safety:
        for s in safety:
            refs = ", ".join(getattr(s, "source_ids", []))
            lines.append(f"- {getattr(s, 'claim', str(s))} [{refs}]")
    else:
        lines.append("(No safety data)")
    lines.append("")

    lines += _claim_section("Unmet Medical Need", getattr(output, "unmet_medical_need", None))

    for section_name, field_name in [
        ("Scientific Risks", "scientific_risks"),
        ("Evidence Gaps", "evidence_gaps"),
        ("Contradictions", "contradictions"),
        ("Uncertainties", "uncertainties"),
        ("Assumptions", "assumptions"),
    ]:
        items = getattr(output, field_name, [])
        lines += [f"## {section_name}", ""]
        if items:
            for item in items:
                lines.append(f"- {item}")
        else:
            lines.append(f"(No {section_name.lower()})")
        lines.append("")

    # Source list
    lines += ["## Sources", ""]
    if sources:
        for i, src in enumerate(sources, 1):
            label = getattr(src, "citation_label", "") or getattr(src, "title", "")
            sid = getattr(src, "source_id", "")
            lines.append(f"{i}. [{sid}] {label}")
    else:
        ids = getattr(output, "source_ids_used", [])
        for sid in ids:
            lines.append(f"- {sid}")
    lines.append("")

    lines += [
        "## Disclaimer",
        "",
        f"> {getattr(output, 'disclaimer', DISCLAIMER)}",
        "",
        AUTO_END,
        "",
    ]

    _write_with_manual_preservation(path, lines)
    return path


def write_source_note(
    source: "SourceRecord",
    vault_dir: Path | None = None,
) -> Path:
    """Create or update a source note in the appropriate vault subfolder."""
    from app.schemas.evidence import SourceType

    vd = ensure_vault_structure(vault_dir)

    type_to_subdir: dict[SourceType, tuple[str, str]] = {
        SourceType.pubmed: ("02_sources/pubmed", "PMID"),
        SourceType.clinicaltrials: ("02_sources/clinicaltrials", "NCT"),
        SourceType.fda: ("02_sources/fda", "FDA"),
        SourceType.ema: ("02_sources/ema", "EMA"),
        SourceType.local_pdf: ("02_sources/pdfs", "PDF"),
        SourceType.orange_book: ("02_sources/orange_book", "OB"),
        SourceType.purple_book: ("02_sources/purple_book", "PB"),
        SourceType.epo_ops: ("02_sources/epo_ops", "EPO"),
        SourceType.uspto: ("02_sources/uspto", "USPTO"),
        SourceType.wipo: ("02_sources/wipo", "WIPO"),
    }

    subdir, prefix = type_to_subdir.get(source.source_type, ("02_sources", "SRC"))
    target_dir = vd / subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    ext_id = source.external_id or source.source_id.split(":", 1)[-1]
    safe_id = slugify(ext_id, fallback=source.source_id.replace(":", "_"))
    filename = f"{prefix}_{safe_id}.md"
    path = target_dir / filename

    front = {
        "type": "source",
        "source_id": source.source_id,
        "source_type": source.source_type.value,
        "title": source.title,
        "external_id": source.external_id or "",
        "publisher": source.publisher or "",
        "retrieved_at": source.retrieved_at,
    }
    if source.publication_date:
        front["publication_date"] = source.publication_date
    if source.url_or_path:
        front["url"] = source.url_or_path

    lines = [
        _frontmatter(front),
        "",
        f"# {source.title}",
        "",
        AUTO_BEGIN,
        "",
        f"- **Source ID:** {source.source_id}",
        f"- **Type:** {source.source_type.value}",
        f"- **External ID:** {source.external_id or 'N/A'}",
        f"- **Publisher:** {source.publisher or 'N/A'}",
        f"- **Publication date:** {source.publication_date or 'N/A'}",
        f"- **Retrieved at:** {source.retrieved_at}",
        f"- **Query used:** {source.query_used}",
        "",
        "## Summary",
        "",
        source.evidence_summary or "(No summary)",
        "",
        "## Citation",
        "",
        source.citation_label or "(No citation)",
        "",
    ]
    if source.reliability_notes:
        lines += ["## Reliability notes", "", source.reliability_notes, ""]
    if source.url_or_path:
        lines += ["## Link", "", source.url_or_path, ""]

    lines += [AUTO_END, ""]

    _write_with_manual_preservation(path, lines)
    return path


def write_market_memo(
    run_id: str,
    output: Any,
    sources: list[Any] | None = None,
    pdf_hashes: dict[str, str] | None = None,
    vault_dir: Path | None = None,
) -> Path:
    """Write a market attractiveness memo to the Obsidian vault."""
    vd = ensure_vault_structure(vault_dir)
    reports_dir = vd / "04_reports" / "market"
    filename = f"{run_id}_market_memo.md"
    path = reports_dir / filename

    front: dict[str, Any] = {
        "type": "market_memo",
        "run_id": run_id,
        "confidence": getattr(output, "confidence", "medium"),
        "created_at": _now_iso(),
        "depends_on": ["scientific_memo"],
    }
    if pdf_hashes:
        front["pdf_hashes"] = pdf_hashes

    lines = [
        _frontmatter(front),
        "",
        f"# Market Memo — {run_id}",
        "",
        AUTO_BEGIN,
        "",
        "## Executive Summary",
        "",
        getattr(output, "market_summary", "") or "(No market summary)",
        "",
    ]

    # Patient Population
    pop = getattr(output, "patient_population", None)
    lines += ["## Patient Population", ""]
    if pop:
        if hasattr(pop, "global_estimate") and pop.global_estimate:
            lines.append(f"- **Global:** {pop.global_estimate}")
        if hasattr(pop, "us_estimate") and pop.us_estimate:
            lines.append(f"- **US:** {pop.us_estimate}")
        if hasattr(pop, "eu_estimate") and pop.eu_estimate:
            lines.append(f"- **EU:** {pop.eu_estimate}")
        if hasattr(pop, "ru_estimate") and pop.ru_estimate:
            lines.append(f"- **RU:** {pop.ru_estimate}")
        if hasattr(pop, "target_segment") and pop.target_segment:
            lines.append(f"- **Target segment:** {pop.target_segment}")
        if hasattr(pop, "segmentation_logic") and pop.segmentation_logic:
            lines.append(f"- **Segmentation logic:** {pop.segmentation_logic}")
    else:
        lines.append("(Not assessed)")
    lines.append("")

    # Treatment Landscape
    treatment = getattr(output, "treatment_landscape", None)
    if treatment:
        lines += ["## Treatment Landscape", "", treatment, ""]

    # Market Size
    market_size = getattr(output, "market_size_estimate", None)
    if market_size:
        lines += ["## Market Size Estimate", "", market_size, ""]

    # Competitors
    competitors = getattr(output, "competitors", [])
    lines += ["## Competitor Landscape", ""]
    if competitors:
        lines.append("| Препарат | Компания | Статус | Механизм | Цена |")
        lines.append("|---|---|---|---|---|")
        for c in competitors:
            name = getattr(c, "drug_name", "?")
            company = getattr(c, "company", "") or ""
            status = getattr(c, "status", "")
            mech = getattr(c, "mechanism", "") or ""
            price = getattr(c, "price_range", "") or ""
            refs = ", ".join(getattr(c, "source_ids", []))
            lines.append(f"| {name} | {company} | {status} | {mech} | {price} |")
            if refs:
                lines[-1] += f" [{refs}]"
    else:
        lines.append("(No competitors identified)")
    lines.append("")

    # Market Dynamics
    dynamics = getattr(output, "market_dynamics", [])
    lines += ["## Market Dynamics", ""]
    if dynamics:
        for d in dynamics:
            direction = getattr(d, "direction", "neutral")
            icon = {"positive": "📈", "negative": "📉", "neutral": "➡️"}.get(direction, "•")
            desc = getattr(d, "description", str(d))
            refs = ", ".join(getattr(d, "source_ids", []))
            line = f"- {icon} {desc}"
            if refs:
                line += f" [{refs}]"
            lines.append(line)
    else:
        lines.append("(Not assessed)")
    lines.append("")

    # Payer Value
    payer = getattr(output, "payer_value", None)
    if payer:
        lines += ["## Payer Value Proposition", "", payer, ""]

    # Pricing Logic
    pricing = getattr(output, "pricing_logic", None)
    if pricing:
        lines += ["## Pricing Logic", "", pricing, ""]

    # Price Benchmarks
    benchmarks = getattr(output, "competitor_price_benchmarks", [])
    if benchmarks:
        lines += ["## Competitor Price Benchmarks", ""]
        lines.append("| Препарат | Цена | Валюта | Путь | Частота |")
        lines.append("|---|---|---|---|---|")
        for b in benchmarks:
            name = getattr(b, "drug_name", "?")
            price = getattr(b, "price_description", "?")
            curr = getattr(b, "currency", "USD")
            route = getattr(b, "route", "") or ""
            freq = getattr(b, "frequency", "") or ""
            lines.append(f"| {name} | {price} | {curr} | {route} | {freq} |")
        lines.append("")

    # Price Sensitivity Analysis
    psa = getattr(output, "price_sensitivity_analysis", None)
    if psa:
        lines += ["## Price Sensitivity Analysis / Анализ ценовой эластичности", ""]
        
        # Reference drug and price
        ref_drug = getattr(psa, "reference_drug", None)
        ref_price = getattr(psa, "reference_price", None)
        if ref_drug:
            lines.append(f"**Референсный препарат:** {ref_drug}")
        if ref_price:
            lines.append(f"**Референсная цена:** {ref_price}")
        lines.append("")
        
        # Scenarios table
        scenarios = getattr(psa, "scenarios", [])
        if scenarios:
            lines.append("### Сценарии ценообразования")
            lines.append("")
            lines.append("| Сценарий | Цена vs конкурент | Ожидаемое принятие | Жизнеспособность |")
            lines.append("|---|---|---|---|")
            for sc in scenarios:
                name = getattr(sc, "scenario_name", "?")
                price_vs = getattr(sc, "price_vs_competitor", "?")
                adoption = getattr(sc, "expected_adoption", "?")
                viability = getattr(sc, "viability", "?")
                adoption_icon = {
                    "very_high": "🟢🟢",
                    "high": "🟢",
                    "moderate": "🟡",
                    "low": "🟠",
                    "very_low": "🔴"
                }.get(adoption, "•")
                viability_icon = {
                    "attractive": "✅",
                    "viable": "🟢",
                    "marginal": "🟡",
                    "not_viable": "🔴"
                }.get(viability, "•")
                lines.append(f"| {name} | {price_vs} | {adoption_icon} {adoption} | {viability_icon} {viability} |")
            lines.append("")
            
            # Detailed rationale for each scenario
            lines.append("### Обоснование по сценариям")
            lines.append("")
            for sc in scenarios:
                name = getattr(sc, "scenario_name", "?")
                rationale = getattr(sc, "adoption_rationale", "")
                payers = getattr(sc, "target_payers", [])
                if rationale:
                    lines.append(f"**{name}:** {rationale}")
                    if payers:
                        lines.append(f"  - Целевые плательщики: {', '.join(payers)}")
            lines.append("")
        
        # Price drivers and barriers
        drivers = getattr(psa, "key_price_drivers", [])
        if drivers:
            lines.append("### Факторы, обосновывающие премиальную цену")
            lines.append("")
            for d in drivers:
                lines.append(f"- ✅ {d}")
            lines.append("")
        
        barriers = getattr(psa, "price_barriers", [])
        if barriers:
            lines.append("### Барьеры для премиального ценообразования")
            lines.append("")
            for b in barriers:
                lines.append(f"- ⚠️ {b}")
            lines.append("")
        
        # Willingness to pay
        wtp = getattr(psa, "willingness_to_pay_assessment", None)
        if wtp:
            lines.append("### Готовность плательщиков платить")
            lines.append("")
            lines.append(wtp)
            lines.append("")
        
        # Price ceiling
        ceiling = getattr(psa, "price_ceiling", None)
        if ceiling:
            lines.append(f"**Ценовой потолок:** {ceiling}")
            lines.append("")
        
        # Conclusion
        conclusion = getattr(psa, "conclusion", None)
        confidence = getattr(psa, "confidence", "low")
        conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(confidence, "•")
        if conclusion:
            lines.append("### Вывод")
            lines.append("")
            lines.append(f"> {conclusion}")
            lines.append("")
            lines.append(f"**Уверенность в оценке:** {conf_icon} {confidence}")
            lines.append("")
        
        # Assumptions
        psa_assumptions = getattr(psa, "assumptions", [])
        if psa_assumptions:
            lines.append("**Допущения:**")
            for a in psa_assumptions:
                lines.append(f"- {a}")
            lines.append("")

    # Differentiation
    diff_opps = getattr(output, "differentiation_opportunities", [])
    if diff_opps:
        lines += ["## Differentiation Opportunities", ""]
        for d in diff_opps:
            lines.append(f"- {d}")
        lines.append("")

    # Commercial Risks
    risks = getattr(output, "commercial_risks", [])
    lines += ["## Commercial Risks", ""]
    if risks:
        for r in risks:
            severity = getattr(r, "severity", "medium")
            icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "•")
            risk_text = getattr(r, "risk", str(r))
            mitigation = getattr(r, "mitigation", None)
            refs = ", ".join(getattr(r, "source_ids", []))
            line = f"- {icon} **{risk_text}**"
            if mitigation:
                line += f"\n  Mitigation: {mitigation}"
            if refs:
                line += f" [{refs}]"
            lines.append(line)
    else:
        lines.append("(No risks identified)")
    lines.append("")

    # Assumptions and missing info
    assumptions = getattr(output, "assumptions", [])
    if assumptions:
        lines += ["## Assumptions", ""]
        for a in assumptions:
            lines.append(f"- {a}")
        lines.append("")

    missing = getattr(output, "missing_information", [])
    if missing:
        lines += ["## Missing Information", ""]
        for m in missing:
            lines.append(f"- {m}")
        lines.append("")

    # Sources
    src_list = getattr(output, "sources", [])
    if src_list:
        lines += ["## Sources", ""]
        for i, s in enumerate(src_list, 1):
            sid = getattr(s, "source_id", "?")
            title = getattr(s, "title", "")
            label = getattr(s, "citation_label", title)
            lines.append(f"{i}. [{sid}] {label}")
        lines.append("")

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


def write_patent_finance_memo(
    run_id: str,
    output: Any,
    sources: list[Any] | None = None,
    pdf_hashes: dict[str, str] | None = None,
    vault_dir: Path | None = None,
) -> Path:
    """Write a patent/finance analysis memo to the Obsidian vault."""
    vd = ensure_vault_structure(vault_dir)
    reports_dir = vd / "04_reports" / "patent_finance"
    reports_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{run_id}_patent_finance_memo.md"
    path = reports_dir / filename

    front: dict[str, Any] = {
        "type": "patent_finance_memo",
        "run_id": run_id,
        "confidence": getattr(output, "confidence", "medium"),
        "legal_review_required": getattr(output, "legal_review_required", True),
        "created_at": _now_iso(),
        "depends_on": ["scientific_memo", "market_memo"],
    }
    if pdf_hashes:
        front["pdf_hashes"] = pdf_hashes

    lines = [
        _frontmatter(front),
        "",
        f"# Patent & Finance Memo — {run_id}",
        "",
        AUTO_BEGIN,
        "",
        "## Executive Summary",
        "",
        getattr(output, "patent_landscape_summary", "") or "(No patent landscape summary)",
        "",
    ]

    # Blocking patents
    blocking = getattr(output, "blocking_patent_candidates", [])
    lines += ["## Potentially Blocking Patents", ""]
    if blocking:
        lines.append("| Patent Number | Title | Assignee | Type | Expiry |")
        lines.append("|---|---|---|---|---|")
        for pat in blocking:
            num = getattr(pat, "patent_number", "?")
            title = (getattr(pat, "title", "") or "")[:60]
            assignee = getattr(pat, "assignee", "") or ""
            ptype = getattr(pat, "patent_type", "") or ""
            exp = getattr(pat, "expiration_date", "") or ""
            lines.append(f"| {num} | {title} | {assignee} | {ptype} | {exp} |")
            rationale = getattr(pat, "blocking_rationale", "")
            if rationale:
                lines.append(f"> {rationale}")
        lines.append("")
    else:
        lines.append("(No blocking patents identified based on available data)")
        lines.append("")

    # Main assignees
    assignees = getattr(output, "main_assignees", [])
    if assignees:
        lines += ["## Main Patent Holders", ""]
        for a in assignees:
            name = getattr(a, "name", "?")
            count = getattr(a, "patent_count", 0)
            lines.append(f"- **{name}**: {count} patents")
        lines.append("")

    # FTO risks
    fto_risks = getattr(output, "freedom_to_operate_risks", [])
    lines += ["## Freedom-to-Operate Risks", ""]
    if fto_risks:
        for risk in fto_risks:
            severity = getattr(risk, "severity", "medium")
            icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "•")
            desc = getattr(risk, "risk_description", str(risk))
            mitigation = getattr(risk, "mitigation_strategy", None)
            refs = ", ".join(getattr(risk, "source_ids", []))
            line = f"- {icon} **{desc}**"
            if mitigation:
                line += f"\n  Mitigation: {mitigation}"
            if refs:
                line += f" [{refs}]"
            lines.append(line)
    else:
        lines.append("(No FTO risks identified based on available data)")
    lines.append("")

    # Patent fence opportunities
    fences = getattr(output, "patent_fence_opportunities", [])
    if fences:
        lines += ["## Patent Fence Opportunities", ""]
        for opp in fences:
            desc = getattr(opp, "opportunity_description", "")
            ptype = getattr(opp, "patent_type", "")
            feas = getattr(opp, "feasibility", "medium")
            icon = {"high": "✅", "medium": "⚠️", "low": "❌"}.get(feas, "•")
            lines.append(f"- {icon} **{desc}** ({ptype})")
        lines.append("")

    # Generic/biosimilar risk
    generic_risk = getattr(output, "generic_or_biosimilar_risk", None)
    if generic_risk:
        lines += ["## Generic/Biosimilar Entry Risk", "", generic_risk, ""]

    # Investment range
    inv_range = getattr(output, "investment_range", None)
    lines += ["## Investment Scenarios", ""]
    if inv_range:
        for case_name in ["low_case", "base_case", "high_case"]:
            case = getattr(inv_range, case_name, None)
            if case:
                amount = getattr(case, "amount_usd", "?")
                assumptions = getattr(case, "assumptions", [])
                lines.append(f"### {case_name.replace('_', ' ').title()}: {amount}")
                if assumptions:
                    lines.append("Assumptions:")
                    for a in assumptions:
                        lines.append(f"- {a}")
                lines.append("")
    else:
        lines.append("(No investment range provided)")
        lines.append("")

    # Cost buckets
    buckets = getattr(output, "major_cost_buckets", [])
    if buckets:
        lines += ["## Major Cost Buckets", ""]
        for b in buckets:
            lines.append(f"- {b}")
        lines.append("")

    # Money timeline
    timeline = getattr(output, "money_timeline", None)
    if timeline:
        lines += ["## Money Timeline", ""]
        for field in ["earliest_value_inflection", "licensing_window", "approval_window", "revenue_window"]:
            val = getattr(timeline, field, None)
            if val:
                label = field.replace("_", " ").title()
                lines.append(f"- **{label}**: {val}")
        scenarios = getattr(timeline, "monetization_scenarios", [])
        if scenarios:
            lines.append("**Monetization Scenarios:**")
            for s in scenarios:
                lines.append(f"- {s}")
        lines.append("")

    # Financial risks
    fin_risks = getattr(output, "key_financial_risks", [])
    lines += ["## Key Financial Risks", ""]
    if fin_risks:
        for risk in fin_risks:
            severity = getattr(risk, "severity", "medium")
            icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity, "•")
            risk_text = getattr(risk, "risk", str(risk))
            mitigation = getattr(risk, "mitigation", None)
            refs = ", ".join(getattr(risk, "source_ids", []))
            line = f"- {icon} **{risk_text}**"
            if mitigation:
                line += f"\n  Mitigation: {mitigation}"
            if refs:
                line += f" [{refs}]"
            lines.append(line)
    else:
        lines.append("(No financial risks identified)")
    lines.append("")

    # Assumptions and missing info
    assumptions = getattr(output, "assumptions", [])
    if assumptions:
        lines += ["## Assumptions", ""]
        for a in assumptions:
            lines.append(f"- {a}")
        lines.append("")

    missing = getattr(output, "missing_information", [])
    if missing:
        lines += ["## Missing Information", ""]
        for m in missing:
            lines.append(f"- {m}")
        lines.append("")

    # Sources
    src_list = getattr(output, "sources", [])
    if src_list:
        lines += ["## Sources", ""]
        for i, s in enumerate(src_list, 1):
            sid = getattr(s, "source_id", "?")
            title = getattr(s, "title", "")
            label = getattr(s, "citation_label", title)
            lines.append(f"{i}. [{sid}] {label}")
        lines.append("")

    # Legal disclaimer
    disclaimer = getattr(output, "disclaimer", "")
    lines += [
        "## ⚠️ Legal Disclaimer",
        "",
        f"> {disclaimer}",
        "",
        "> **IMPORTANT**: This is a preliminary AI-assisted analysis. "
        "A qualified patent attorney and financial analyst must review before making business decisions.",
        "",
        AUTO_END,
        "",
    ]

    _write_with_manual_preservation(path, lines)
    return path


def write_patent_evidence_note(
    patent: "PatentEvidence",
    run_id: str,
    vault_dir: Path | None = None,
) -> Path:
    """Create or update a patent source note in the Obsidian vault.

    Files are written to 02_sources/patents/{connector_name}/ based on
    the patent's source_type (rospatent, fips, fips_registers, eapo, etc.).
    """
    vd = ensure_vault_structure(vault_dir)

    # Determine subdirectory based on source type
    connector_name = patent.source_type or "unknown"
    patent_dir = vd / "02_sources" / "patents" / connector_name
    patent_dir.mkdir(parents=True, exist_ok=True)

    # Build filename: {jurisdiction}_{document_number}.md
    safe_doc = slugify(
        patent.document_number,
        fallback=patent.source_id.replace(":", "_"),
    )
    filename = f"{patent.jurisdiction}_{safe_doc}.md"
    path = patent_dir / filename

    front: dict[str, Any] = {
        "type": "patent_source",
        "source_id": patent.source_id,
        "source_type": patent.source_type,
        "jurisdiction": patent.jurisdiction,
        "document_number": patent.document_number,
        "title": patent.title,
        "legal_status": patent.legal_status.value,
        "blocking_risk": patent.blocking_risk_preliminary.value,
        "retrieved_at": patent.retrieved_at,
        "source_runs": [run_id],
    }
    if patent.application_number:
        front["application_number"] = patent.application_number
    if patent.publication_number:
        front["publication_number"] = patent.publication_number
    if patent.filing_date:
        front["filing_date"] = patent.filing_date
    if patent.priority_date:
        front["priority_date"] = patent.priority_date
    if patent.publication_date:
        front["publication_date"] = patent.publication_date
    if patent.grant_date:
        front["grant_date"] = patent.grant_date

    lines = [
        _frontmatter(front),
        "",
        f"# {patent.title}",
        "",
        AUTO_BEGIN,
        "",
        "## Patent Identity",
        "",
        f"- **Document Number:** {patent.document_number}",
        f"- **Jurisdiction:** {patent.jurisdiction}",
        f"- **Application Number:** {patent.application_number or 'N/A'}",
        f"- **Publication Number:** {patent.publication_number or 'N/A'}",
        "",
        "## Dates",
        "",
        f"- **Filing Date:** {patent.filing_date or 'N/A'}",
        f"- **Priority Date:** {patent.priority_date or 'N/A'}",
        f"- **Publication Date:** {patent.publication_date or 'N/A'}",
        f"- **Grant Date:** {patent.grant_date or 'N/A'}",
        "",
    ]

    if patent.applicants:
        lines += ["## Applicants", ""]
        for a in patent.applicants:
            lines.append(f"- {a}")
        lines.append("")

    if patent.patent_holders:
        lines += ["## Patent Holders", ""]
        for h in patent.patent_holders:
            lines.append(f"- {h}")
        lines.append("")

    if patent.inventors:
        lines += ["## Inventors", ""]
        for i in patent.inventors:
            lines.append(f"- {i}")
        lines.append("")

    # Classification
    lines += ["## Classification", ""]
    if patent.ipc_codes:
        lines.append(f"- **IPC:** {', '.join(patent.ipc_codes)}")
    else:
        lines.append("- **IPC:** N/A")
    if patent.cpc_codes:
        lines.append(f"- **CPC:** {', '.join(patent.cpc_codes)}")
    else:
        lines.append("- **CPC:** N/A")
    lines.append("")

    # Legal Status
    status_icon = {
        "active": "✅",
        "expired": "⚪",
        "lapsed": "❌",
        "terminated": "❌",
        "pending": "🟡",
        "withdrawn": "⚪",
        "rejected": "❌",
        "unknown": "❓",
    }.get(patent.legal_status.value, "❓")
    lines += [
        "## Legal Status",
        "",
        f"{status_icon} **{patent.legal_status.value.upper()}**",
        "",
    ]

    # Blocking Risk
    risk_icon = {
        "high": "🔴",
        "medium": "🟡",
        "low": "🟢",
        "unknown": "❓",
    }.get(patent.blocking_risk_preliminary.value, "❓")
    lines += [
        "## Blocking Risk Assessment",
        "",
        f"{risk_icon} **{patent.blocking_risk_preliminary.value.upper()}**",
        "",
    ]

    # Relevance
    if patent.relevance_reason:
        lines += [
            "## Relevance",
            "",
            patent.relevance_reason,
            "",
        ]

    # Patent Types
    if patent.patent_types:
        lines += ["## Patent Type Classification", ""]
        for pt in patent.patent_types:
            lines.append(f"- {pt.value}")
        lines.append("")

    # Abstract
    if patent.abstract:
        lines += [
            "## Abstract",
            "",
            patent.abstract[:2000] if len(patent.abstract) > 2000 else patent.abstract,
            "",
        ]

    # Source URL
    if patent.source_url:
        lines += [
            "## Source",
            "",
            f"[{patent.source_url}]({patent.source_url})",
            "",
        ]

    # Warnings
    if patent.warnings:
        lines += ["## ⚠️ Warnings", ""]
        for w in patent.warnings:
            lines.append(f"- {w}")
        lines.append("")

    # Raw metadata (collapsed)
    if patent.raw_metadata:
        lines += [
            "## Raw Metadata",
            "",
            "```json",
            str(patent.raw_metadata)[:1000],
            "```",
            "",
        ]

    lines += [
        "## Disclaimer",
        "",
        "> This automated patent analysis is preliminary and does not constitute a legal freedom-to-operate opinion. The results must be reviewed by a qualified patent attorney before any development, licensing, or commercialization decision.",
        "",
        AUTO_END,
        "",
        "## Linked Runs",
        f"- [[{run_id}]]",
        "",
    ]

    _write_with_manual_preservation(path, lines)
    return path


def write_patent_family_note(
    family: "PatentFamilyEvidence",
    run_id: str,
    vault_dir: Path | None = None,
) -> Path:
    """Create or update a patent family note in the Obsidian vault.

    Family notes are written to 02_sources/patents/families/.
    """
    vd = ensure_vault_structure(vault_dir)
    family_dir = vd / "02_sources" / "patents" / "families"
    family_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{family.family_id}.md"
    path = family_dir / filename

    front: dict[str, Any] = {
        "type": "patent_family",
        "family_id": family.family_id,
        "jurisdictions": family.jurisdictions,
        "member_count": len(family.members),
        "highest_blocking_risk": family.highest_blocking_risk.value,
        "blocking_jurisdictions": family.blocking_jurisdictions,
        "patent_types": [pt.value for pt in family.patent_types],
        "source_runs": [run_id],
    }
    if family.priority_number:
        front["priority_number"] = family.priority_number
    if family.earliest_priority_date:
        front["earliest_priority_date"] = family.earliest_priority_date

    lines = [
        _frontmatter(front),
        "",
        f"# Patent Family: {family.family_id}",
        "",
        AUTO_BEGIN,
        "",
        "## Overview",
        "",
        f"- **Jurisdictions:** {', '.join(family.jurisdictions)}",
        f"- **Members:** {len(family.members)} patents",
        f"- **Earliest Priority:** {family.earliest_priority_date or 'N/A'}",
        "",
    ]

    # Risk assessment
    risk_icon = {
        "high": "🔴",
        "medium": "🟡",
        "low": "🟢",
        "unknown": "❓",
    }.get(family.highest_blocking_risk.value, "❓")
    lines += [
        "## Blocking Risk",
        "",
        f"{risk_icon} **{family.highest_blocking_risk.value.upper()}**",
        "",
    ]
    if family.blocking_jurisdictions:
        lines.append("**Blocking jurisdictions:** " + ", ".join(family.blocking_jurisdictions))
        lines.append("")

    # Main applicants
    if family.main_applicants:
        lines += ["## Main Applicants", ""]
        for a in family.main_applicants:
            lines.append(f"- {a}")
        lines.append("")

    # Patent Types
    if family.patent_types:
        lines += ["## Patent Types", ""]
        for pt in family.patent_types:
            lines.append(f"- {pt.value}")
        lines.append("")

    # Members table
    lines += ["## Family Members", ""]
    lines.append("| Jurisdiction | Document | Title | Status | Risk |")
    lines.append("|---|---|---|---|---|")
    for member in family.members:
        status_icon = {
            "active": "✅",
            "expired": "⚪",
            "lapsed": "❌",
            "terminated": "❌",
            "pending": "🟡",
            "withdrawn": "⚪",
            "rejected": "❌",
            "unknown": "❓",
        }.get(member.legal_status.value, "❓")
        risk_icon = {
            "high": "🔴",
            "medium": "🟡",
            "low": "🟢",
            "unknown": "❓",
        }.get(member.blocking_risk_preliminary.value, "❓")
        title_short = (member.title or "")[:40]
        lines.append(
            f"| {member.jurisdiction} | {member.document_number} | "
            f"{title_short} | {status_icon} {member.legal_status.value} | "
            f"{risk_icon} {member.blocking_risk_preliminary.value} |"
        )
    lines.append("")

    # Links to individual patent notes
    lines += ["## Individual Patent Notes", ""]
    for member in family.members:
        safe_doc = slugify(
            member.document_number,
            fallback=member.source_id.replace(":", "_"),
        )
        note_path = f"02_sources/patents/{member.source_type}/{member.jurisdiction}_{safe_doc}"
        lines.append(f"- [[{note_path}|{member.jurisdiction} {member.document_number}]]")
    lines.append("")

    lines += [
        "## Disclaimer",
        "",
        "> This automated patent analysis is preliminary and does not constitute a legal freedom-to-operate opinion.",
        "",
        AUTO_END,
        "",
        "## Linked Runs",
        f"- [[{run_id}]]",
        "",
    ]

    _write_with_manual_preservation(path, lines)
    return path


def write_patent_aggregator_report(
    run_id: str,
    result: "AggregatedPatentResult",
    vault_dir: Path | None = None,
) -> Path:
    """Write a summary report of the patent aggregator search to Obsidian.

    This is a high-level diagnostic report written to 04_reports/patent_finance/.
    """
    vd = ensure_vault_structure(vault_dir)
    reports_dir = vd / "04_reports" / "patent_finance"
    reports_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{run_id}_patent_search_report.md"
    path = reports_dir / filename

    front: dict[str, Any] = {
        "type": "patent_search_report",
        "run_id": run_id,
        "query_inn": result.query.inn,
        "query_indication": result.query.indication,
        "total_patents": len(result.all_patents),
        "total_families": len(result.patent_families),
        "sources_available": result.sources_available,
        "sources_unavailable": result.sources_unavailable,
        "requires_manual_review": result.requires_manual_review,
        "created_at": _now_iso(),
    }

    lines = [
        _frontmatter(front),
        "",
        f"# Patent Search Report — {run_id}",
        "",
        AUTO_BEGIN,
        "",
        "## Search Query",
        "",
        f"- **INN:** {result.query.inn}",
        f"- **Indication:** {result.query.indication or 'N/A'}",
        f"- **Search Terms:** {', '.join(result.query.get_all_search_terms())}",
        "",
        "## Source Diagnostics",
        "",
        "| Source | Status |",
        "|---|---|",
    ]

    for src in result.sources_queried:
        status = "✅ Available" if src in result.sources_available else "❌ Unavailable"
        lines.append(f"| {src} | {status} |")
    lines.append("")

    # Summary statistics
    lines += [
        "## Results Summary",
        "",
        f"- **Total Patents Found:** {len(result.all_patents)}",
        f"- **Patent Families:** {len(result.patent_families)}",
        f"- **RU Patents:** {sum(1 for p in result.all_patents if p.jurisdiction == 'RU')}",
        f"- **EA Patents:** {sum(1 for p in result.all_patents if p.jurisdiction == 'EA')}",
        f"- **EP Patents:** {sum(1 for p in result.all_patents if p.jurisdiction == 'EP')}",
        f"- **US Patents:** {sum(1 for p in result.all_patents if p.jurisdiction == 'US')}",
        "",
    ]

    # Legal status breakdown
    from collections import Counter
    status_counts = Counter(p.legal_status.value for p in result.all_patents)
    if status_counts:
        lines += ["### Legal Status Breakdown", ""]
        for status, count in sorted(status_counts.items()):
            lines.append(f"- {status}: {count}")
        lines.append("")

    # Manual review flag
    if result.requires_manual_review:
        lines += [
            "## ⚠️ Manual Review Required",
            "",
        ]
        for reason in result.manual_review_reasons:
            lines.append(f"- {reason}")
        lines.append("")

    # Warnings
    if result.total_warnings:
        lines += ["## Warnings", ""]
        for w in result.total_warnings:
            lines.append(f"- {w}")
        lines.append("")

    # Per-source results
    if result.rospatent_results:
        lines += ["## Rospatent Results", ""]
        lines.append(f"- Results returned: {result.rospatent_results.results_returned}")
        if result.rospatent_results.warnings:
            lines.append("- Warnings:")
            for w in result.rospatent_results.warnings:
                lines.append(f"  - {w}")
        lines.append("")

    if result.fips_results:
        lines += ["## FIPS Results", ""]
        lines.append(f"- Results returned: {result.fips_results.results_returned}")
        if result.fips_results.warnings:
            lines.append("- Warnings:")
            for w in result.fips_results.warnings:
                lines.append(f"  - {w}")
        lines.append("")

    if result.eapo_results:
        lines += ["## EAPO Results", ""]
        lines.append(f"- Results returned: {result.eapo_results.results_returned}")
        if result.eapo_results.warnings:
            lines.append("- Warnings:")
            for w in result.eapo_results.warnings:
                lines.append(f"  - {w}")
        lines.append("")

    # Patent families overview
    if result.patent_families:
        lines += ["## Patent Families", ""]
        for family in result.patent_families:
            risk_icon = {
                "high": "🔴",
                "medium": "🟡",
                "low": "🟢",
                "unknown": "❓",
            }.get(family.highest_blocking_risk.value, "❓")
            lines.append(
                f"- {risk_icon} **{family.family_id}**: "
                f"{len(family.members)} patents in {', '.join(family.jurisdictions)}"
            )
        lines.append("")

    lines += [
        "## Disclaimer",
        "",
        "> This automated patent analysis is preliminary and does not constitute a legal freedom-to-operate opinion. The results must be reviewed by a qualified patent attorney before any development, licensing, or commercialization decision.",
        "",
        AUTO_END,
        "",
    ]

    _write_with_manual_preservation(path, lines)
    return path


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
