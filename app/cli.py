from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer

from app.agents.intake_enrichment_agent import IntakeEnrichmentAgent
from app.config import config
from app.orchestrator import Orchestrator
from app.schemas.human_decision import HumanDecision
from app.schemas.input import RawInput
from app.schemas.run import RunStatus
from app.storage.db import Database

app = typer.Typer(help="pharm-agent MVP 1 — deterministic pharma analysis skeleton")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """Create and execute a new analysis run."""
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
    record = orchestrator.run(raw, [pdf1, pdf2])

    typer.echo(f"Run created: {record.run_id}")
    typer.echo(f"Status: {record.status.value}")

    if record.status == RunStatus.awaiting_human_verification:
        typer.echo("")
        typer.echo("=== Human Verification Required ===")
        packet = orchestrator.build_verification_packet(record.run_id)
        typer.echo(f"Run ID: {packet.run_id}")
        typer.echo(f"Raw INN: {packet.raw_inn}")
        if packet.raw_disease:
            typer.echo(f"Raw Disease: {packet.raw_disease}")
        typer.echo("")
        typer.echo("--- Normalized INN ---")
        typer.echo(f"Preferred name: {packet.normalized_inn.preferred_name if hasattr(packet.normalized_inn, 'preferred_name') else packet.normalized_inn.get('preferred_name')}")
        typer.echo("")
        typer.echo("--- Ambiguities ---")
        for a in packet.ambiguities:
            typer.echo(f"  - {a}")
        typer.echo("")
        typer.echo("--- Questions ---")
        for q in packet.questions:
            typer.echo(f"  - {q}")
        typer.echo("")
        typer.echo("--- PDF Extraction ---")
        for pid, st in packet.pdf_extraction_status.items():
            typer.echo(f"  {pid}: {st}")
        typer.echo("")
        typer.echo("Please review and then run:")
        typer.echo(f"  python -m app.cli verify --run-id {record.run_id} --decision approved")
        typer.echo(f"  python -m app.cli verify --run-id {record.run_id} --decision rejected")
        typer.echo(f"  python -m app.cli verify --run-id {record.run_id} --decision needs_revision")


@app.command()
def verify(
    run_id: str = typer.Option(..., help="Run ID to verify"),
    decision: str = typer.Option(..., help="approved | rejected | needs_revision"),
    comment: str | None = typer.Option(None, help="Optional comment"),
    reviewer: str | None = typer.Option(None, help="Reviewer name"),
    corrections_json: str | None = typer.Option(None, help='JSON corrections, e.g. {"inn_raw":"..."}'),
) -> None:
    """Submit a human verification decision for a run."""
    if decision not in ("approved", "rejected", "needs_revision"):
        typer.echo("Error: decision must be approved, rejected, or needs_revision", err=True)
        raise typer.Exit(code=1)

    config.ensure_dirs()
    db = Database()
    db.init_schema()
    orchestrator = Orchestrator(db=db)

    corrections = {}
    if corrections_json:
        import json
        try:
            corrections = json.loads(corrections_json)
        except Exception as exc:
            typer.echo(f"Invalid corrections JSON: {exc}", err=True)
            raise typer.Exit(code=1)

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
    if run.error_message:
        typer.echo(f"Error: {run.error_message}")


if __name__ == "__main__":
    app()
