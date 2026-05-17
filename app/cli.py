from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import typer

from app.config import config
from app.orchestrator import Orchestrator
from app.schemas.human_decision import HumanDecision
from app.schemas.input import RawInput
from app.schemas.run import RunStatus
from app.storage.db import Database

DISCLAIMER = (
    "This analysis is for R&D and investment research only. "
    "It is not medical advice, clinical guidance, or a substitute "
    "for qualified professional review."
)

app = typer.Typer(help="pharm-agent MVP 1 — deterministic pharma analysis skeleton")


def _setup_debug(debug: bool) -> None:
    """Configure debug logging and config flag."""
    if debug:
        config.debug = True
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        logging.getLogger("pharm_agent").setLevel(logging.DEBUG)
        logging.getLogger("httpx").setLevel(logging.INFO)
        typer.echo("  [DEBUG mode enabled]")
    else:
        logging.basicConfig(level=logging.WARNING)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _print_section(title: str) -> None:
    typer.echo("")
    typer.echo(f"{'=' * 60}")
    typer.echo(f"  {title}")
    typer.echo(f"{'=' * 60}")


def _print_verification_packet(packet) -> None:  # noqa: ANN001
    """Pretty-print the enrichment summary for human review."""
    _print_section("ENRICHMENT SUMMARY / РЕЗУЛЬТАТ ОБОГАЩЕНИЯ")

    typer.echo(f"\n  Run ID:      {packet.run_id}")
    typer.echo(f"  Raw INN:     {packet.raw_inn}")
    if packet.raw_disease:
        typer.echo(f"  Raw Disease: {packet.raw_disease}")
    typer.echo(f"  Completeness: {packet.completeness}")

    if packet.completeness == "low":
        typer.echo("")
        typer.echo("  ⚠ WARNING: Completeness is LOW. Critical data is missing.")
        typer.echo("  The system will ask you to provide clarifications before proceeding.")
    elif packet.completeness == "medium" and packet.questions:
        typer.echo("  ℹ Some clarifications may be needed — see questions below.")

    typer.echo("\n--- Normalized INN / Нормализованный МНН ---")
    inn = packet.normalized_inn
    typer.echo(f"  Preferred name: {inn.preferred_name}")
    if inn.english_inn:
        typer.echo(f"  English INN:    {inn.english_inn}")
    if inn.russian_name:
        typer.echo(f"  Russian name:   {inn.russian_name}")
    if inn.synonyms:
        typer.echo(f"  Synonyms:       {', '.join(inn.synonyms)}")
    if inn.brand_names:
        typer.echo(f"  Brand names:    {', '.join(inn.brand_names)}")
    typer.echo(f"  Molecule type:  {inn.molecule_type}")
    typer.echo(f"  Confidence:     {inn.confidence}")

    if packet.normalized_disease:
        typer.echo("\n--- Normalized Disease / Нормализованное заболевание ---")
        dis = packet.normalized_disease
        typer.echo(f"  Preferred name: {dis.preferred_name}")
        if dis.synonyms:
            typer.echo(f"  Synonyms:       {', '.join(dis.synonyms)}")
        if dis.subtypes:
            typer.echo(f"  Subtypes:       {', '.join(dis.subtypes)}")
        typer.echo(f"  Confidence:     {dis.confidence}")

    if packet.ambiguities:
        typer.echo("\n--- Ambiguities / Неоднозначности ---")
        for a in packet.ambiguities:
            typer.echo(f"  - {a}")

    if packet.assumptions:
        typer.echo("\n--- Assumptions / Допущения ---")
        for a in packet.assumptions:
            typer.echo(f"  - {a}")

    if packet.missing_information:
        typer.echo("\n--- Missing Information / Недостающая информация ---")
        for m in packet.missing_information:
            typer.echo(f"  - {m}")

    if packet.questions:
        typer.echo("\n--- Questions for Reviewer / Вопросы для рецензента ---")
        for q in packet.questions:
            typer.echo(f"  ? {q}")

    typer.echo("\n--- PDF Extraction Status ---")
    for pid, st in packet.pdf_extraction_status.items():
        typer.echo(f"  {pid}: {st}")

    typer.echo("")


@app.command()
def run(
    inn: str = typer.Option(..., help="МНН / INN (required)"),
    disease: str | None = typer.Option(None, help="Заболевание / indication (optional but recommended)"),
    pdf1: Path = typer.Option(..., help="Path to first PDF file"),
    pdf2: Path = typer.Option(..., help="Path to second PDF file"),
    region: str | None = typer.Option(None, help="Регион: global | US | EU | RU | custom"),
    molecule_type: str | None = typer.Option(None, help="Тип молекулы или формулировки"),
    stage: str | None = typer.Option(None, help="Стадия разработки"),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
) -> None:
    """Create and execute a new analysis run (end-to-end MVP 1 flow)."""
    _setup_debug(debug)
    config.ensure_dirs()
    db = Database()
    db.init_schema()

    pdf1 = pdf1.resolve()
    pdf2 = pdf2.resolve()
    for p in (pdf1, pdf2):
        if not p.exists():
            typer.echo(f"Error: PDF not found: {p}", err=True)
            raise typer.Exit(code=1)

    raw = RawInput(
        inn_raw=inn,
        disease_raw=disease,
        region=region,
        molecule_type=molecule_type,
        stage=stage,
    )

    orchestrator = Orchestrator(db=db)

    # Phase 1: create run -> hash -> PDFs -> enrich -> await verification
    record, packet = orchestrator.run_until_verification(raw, [pdf1, pdf2])

    typer.echo(f"\nRun created: {record.run_id}")

    if record.status == RunStatus.failed:
        typer.echo(f"Status: {record.status.value}")
        if record.error_message:
            typer.echo(f"Error: {record.error_message}")
        typer.echo(f"\n{DISCLAIMER}")
        raise typer.Exit(code=1)

    # Phase 2: display enrichment and ask for human approval inline
    if packet is not None:
        _print_verification_packet(packet)

    _print_section("HUMAN VERIFICATION REQUIRED / ТРЕБУЕТСЯ ВЕРИФИКАЦИЯ")
    typer.echo("  Please review the enrichment summary above.")

    if packet and packet.completeness == "low":
        typer.echo("")
        typer.echo("  ⚠ Completeness: LOW — critical information is missing.")
        typer.echo("  The system strongly recommends requesting revision before proceeding.")
        typer.echo("")

    typer.echo("  Options: [a]pprove / [r]eject / [n]eeds revision")
    typer.echo("")

    choice = typer.prompt("  Your decision (a/r/n)", default="a").strip().lower()

    if choice in ("r", "reject", "rejected"):
        comment = typer.prompt("  Comment (optional, press Enter to skip)", default="").strip() or None
        decision = HumanDecision(
            run_id=record.run_id,
            decision="rejected",
            comments=comment,
            timestamp=_now_iso(),
        )
        record, _ = orchestrator.finalize_decision(record.run_id, decision)
        typer.echo(f"\n  Run {record.run_id} rejected.")
        typer.echo(f"  Final status: {record.status.value}")
        typer.echo(f"\n{DISCLAIMER}")
        return

    if choice in ("n", "needs_revision", "revision"):
        typer.echo("\n  Please specify what needs to be corrected:")
        corrections_raw = typer.prompt("  Corrections (JSON or free text)", default="").strip()
        comment = typer.prompt("  Comment (optional, press Enter to skip)", default="").strip() or None

        corrections: dict[str, str] = {}
        if corrections_raw:
            try:
                corrections = json.loads(corrections_raw)
            except json.JSONDecodeError:
                corrections = {"user_feedback": corrections_raw}

        decision = HumanDecision(
            run_id=record.run_id,
            decision="needs_revision",
            corrections=corrections,
            comments=comment,
            timestamp=_now_iso(),
        )
        record, _ = orchestrator.finalize_decision(record.run_id, decision)
        typer.echo(f"\n  Run {record.run_id} sent back for revision.")
        typer.echo(f"  Status: {record.status.value}")
        typer.echo("  Re-run the analysis with corrected input when ready.")
        typer.echo(f"\n{DISCLAIMER}")
        return

    # Approved
    if packet and packet.completeness == "low":
        confirm = typer.confirm(
            "  Completeness is LOW. Are you sure you want to approve?", default=False
        )
        if not confirm:
            typer.echo("  Approval cancelled. Please re-run with option [n]eeds revision.")
            typer.echo(f"\n{DISCLAIMER}")
            return

    comment = typer.prompt("  Comment (optional, press Enter to skip)", default="").strip() or None
    decision = HumanDecision(
        run_id=record.run_id,
        decision="approved",
        comments=comment,
        timestamp=_now_iso(),
    )
    record, summary = orchestrator.finalize_decision(record.run_id, decision)

    _print_section("RUN COMPLETED / ЗАПУСК ЗАВЕРШЁН")
    typer.echo(f"  Run ID:   {record.run_id}")
    typer.echo(f"  Status:   {record.status.value}")
    if summary:
        typer.echo(f"\n  INN:      {summary.inn_preferred}")
        if summary.inn_english:
            typer.echo(f"  INN (EN): {summary.inn_english}")
        if summary.disease_preferred:
            typer.echo(f"  Disease:  {summary.disease_preferred}")
        typer.echo(f"  Input hash: {summary.input_hash[:16]}...")
        for pid, h in summary.pdf_hashes.items():
            typer.echo(f"  {pid} hash: {h[:16]}...")
    typer.echo(f"\n{DISCLAIMER}")


@app.command()
def verify(
    run_id: str = typer.Option(..., help="Run ID to verify"),
    decision: str = typer.Option(..., help="approved | rejected | needs_revision"),
    comment: str | None = typer.Option(None, help="Optional comment"),
    reviewer: str | None = typer.Option(None, help="Reviewer name"),
    corrections_json: str | None = typer.Option(None, help='JSON corrections, e.g. {"inn_raw":"..."}'),
) -> None:
    """Submit a human verification decision for an existing run (alternative to inline approval)."""
    if decision not in ("approved", "rejected", "needs_revision"):
        typer.echo("Error: decision must be approved, rejected, or needs_revision", err=True)
        raise typer.Exit(code=1)

    config.ensure_dirs()
    db = Database()
    db.init_schema()
    orchestrator = Orchestrator(db=db)

    corrections = {}
    if corrections_json:
        try:
            corrections = json.loads(corrections_json)
        except Exception as exc:
            typer.echo(f"Invalid corrections JSON: {exc}", err=True)
            raise typer.Exit(code=1) from None

    hd = HumanDecision(
        run_id=run_id,
        decision=decision,  # type: ignore[arg-type]
        corrections=corrections,
        comments=comment,
        reviewer_name=reviewer,
        timestamp=_now_iso(),
    )
    record = orchestrator.submit_human_decision(run_id, hd)
    typer.echo(f"Run {run_id} updated to status: {record.status.value}")


@app.command()
def status(run_id: str = typer.Option(..., help="Run ID to query")) -> None:
    """Check the status of a run."""
    db = Database()
    db.init_schema()
    run = db.get_run(run_id)
    if run is None:
        typer.echo(f"Run {run_id} not found", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Status: {run.status.value}")
    typer.echo(f"Created: {run.created_at}")
    typer.echo(f"Updated: {run.updated_at}")
    if run.input_hash:
        typer.echo(f"Input hash: {run.input_hash[:16]}...")
    if run.error_message:
        typer.echo(f"Error: {run.error_message}")


@app.command()
def revise(
    run_id: str = typer.Option(..., help="Run ID in needs_revision status"),
    inn: str = typer.Option(..., help="Corrected МНН / INN"),
    disease: str | None = typer.Option(None, help="Corrected disease / indication"),
    pdf1: Path = typer.Option(..., help="Path to first PDF file"),
    pdf2: Path = typer.Option(..., help="Path to second PDF file"),
    region: str | None = typer.Option(None, help="Region"),
    molecule_type: str | None = typer.Option(None, help="Molecule type"),
    stage: str | None = typer.Option(None, help="Development stage"),
) -> None:
    """Re-run enrichment for a run that was sent back for revision."""
    config.ensure_dirs()
    db = Database()
    db.init_schema()

    pdf1 = pdf1.resolve()
    pdf2 = pdf2.resolve()
    for p in (pdf1, pdf2):
        if not p.exists():
            typer.echo(f"Error: PDF not found: {p}", err=True)
            raise typer.Exit(code=1)

    corrected = RawInput(
        inn_raw=inn,
        disease_raw=disease,
        region=region,
        molecule_type=molecule_type,
        stage=stage,
    )

    orchestrator = Orchestrator(db=db)

    try:
        record, packet = orchestrator.rerun_from_revision(run_id, corrected, [pdf1, pdf2])
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None

    if record.status == RunStatus.failed:
        typer.echo(f"Status: {record.status.value}")
        if record.error_message:
            typer.echo(f"Error: {record.error_message}")
        typer.echo(f"\n{DISCLAIMER}")
        raise typer.Exit(code=1)

    if packet is not None:
        _print_verification_packet(packet)

    _print_section("REVISION COMPLETE — VERIFICATION REQUIRED")
    typer.echo("  Enrichment re-run complete. Please verify the updated results.")
    typer.echo("  Use the 'run' command flow or 'verify' command to approve/reject.")
    typer.echo(f"\n{DISCLAIMER}")


if __name__ == "__main__":
    app()
