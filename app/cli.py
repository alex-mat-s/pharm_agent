from __future__ import annotations

import json
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
) -> None:
    """Create and execute a new analysis run (end-to-end MVP 1 flow)."""
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
    typer.echo("  Options: [a]pprove / [r]eject")
    typer.echo("")

    choice = typer.prompt("  Your decision (a/r)", default="a").strip().lower()
    comment = None
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

    # Approved
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


if __name__ == "__main__":
    app()
