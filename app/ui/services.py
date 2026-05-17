"""Service layer: thin bridge between Gradio UI handlers and the backend.

All real pipeline/agent logic stays in orchestrator, agents, connectors.
This module only adapts backend calls for UI consumption.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import config
from app.orchestrator import Orchestrator
from app.schemas.human_decision import HumanDecision
from app.schemas.input import RawInput
from app.schemas.intake_output import HumanVerificationPacket
from app.schemas.run import RunStatus
from app.storage.db import Database

logger = logging.getLogger("pharm_agent.ui.services")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _db() -> Database:
    config.ensure_dirs()
    db = Database()
    db.init_schema()
    return db


def _orchestrator(db: Database | None = None) -> Orchestrator:
    return Orchestrator(db=db or _db())


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


_SECRET_PATTERN = re.compile(r"(sk-or-[a-zA-Z0-9_-]{10,}|Bearer\s+\S+)", re.IGNORECASE)


def mask_secrets(text: str) -> str:
    """Mask API keys and bearer tokens in text."""
    return _SECRET_PATTERN.sub("[REDACTED]", text)


# ─── Data classes for UI responses ───────────────────────────────────────────

@dataclass
class RunCreateResult:
    success: bool
    run_id: str | None = None
    status: str | None = None
    enrichment_json: str | None = None
    enrichment_summary: str | None = None
    error: str | None = None


@dataclass
class DecisionResult:
    success: bool
    run_id: str | None = None
    status: str | None = None
    error: str | None = None
    next_action: str = ""


@dataclass
class PDFHashInfo:
    pdf_id: str
    filename: str
    sha256: str
    version_status: str


# ─── Tab 1: New Analysis ─────────────────────────────────────────────────────

def create_run_and_enrich(
    inn: str,
    disease: str | None,
    region: str | None,
    stage: str | None,
    pdf1_path: str | None,
    pdf2_path: str | None,
    analyst_notes: str | None = None,
) -> RunCreateResult:
    """Create a new run, register PDFs, run intake enrichment."""
    logger.info("create_run_and_enrich: inn=%r, disease=%r, pdf1=%r, pdf2=%r",
                inn, disease, pdf1_path, pdf2_path)

    inn = (inn or "").strip()
    if not inn:
        return RunCreateResult(success=False, error="МНН / INN обязателен.")

    if not pdf1_path or not pdf2_path:
        return RunCreateResult(success=False, error="Необходимо загрузить ровно 2 PDF файла.")

    pdf1 = Path(pdf1_path)
    pdf2 = Path(pdf2_path)

    # Copy uploaded PDFs to configured pdfs dir
    target_dir = config.pdfs_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    pdf_paths: list[Path] = []
    for src in (pdf1, pdf2):
        if not src.exists():
            return RunCreateResult(success=False, error=f"PDF файл не найден: {src.name}")
        dst = target_dir / src.name
        if dst != src:
            shutil.copy2(str(src), str(dst))
        pdf_paths.append(dst.resolve())

    raw = RawInput(
        inn_raw=inn,
        disease_raw=(disease or "").strip() or None,
        region=(region or "").strip() or None,
        stage=(stage or "").strip() or None,
    )

    db = _db()
    orchestrator = _orchestrator(db)

    try:
        record, packet = orchestrator.run_until_verification(raw, pdf_paths)
    except Exception as exc:
        logger.exception("create_run_and_enrich failed")
        return RunCreateResult(success=False, error=f"{type(exc).__name__}: {exc}")

    if record.status == RunStatus.failed:
        return RunCreateResult(
            success=False,
            run_id=record.run_id,
            status=record.status.value,
            error=record.error_message or "Неизвестная ошибка при обогащении.",
        )

    enrichment_json = record.enrichment_output_json or "{}"
    summary = _build_enrichment_summary(enrichment_json, packet)

    return RunCreateResult(
        success=True,
        run_id=record.run_id,
        status=record.status.value,
        enrichment_json=enrichment_json,
        enrichment_summary=summary,
    )


def _build_enrichment_summary(enrichment_json: str, packet: HumanVerificationPacket | None) -> str:
    """Build a human-readable markdown summary of the enrichment."""
    try:
        data = json.loads(enrichment_json)
    except json.JSONDecodeError:
        return "Ошибка разбора enrichment JSON."

    lines: list[str] = ["## Результат обогащения\n"]
    inn = data.get("normalized_inn", {})
    if inn:
        lines.append(f"**INN (preferred):** {inn.get('preferred_name', '?')}")
        if inn.get("english_inn"):
            lines.append(f"**English INN:** {inn['english_inn']}")
        if inn.get("russian_name"):
            lines.append(f"**МНН (русский):** {inn['russian_name']}")
        if inn.get("synonyms"):
            lines.append(f"**Синонимы:** {', '.join(inn['synonyms'])}")
        if inn.get("brand_names"):
            lines.append(f"**Бренды:** {', '.join(inn['brand_names'])}")
        lines.append(f"**Тип молекулы:** {inn.get('molecule_type', '?')}")
        lines.append(f"**Уверенность:** {inn.get('confidence', '?')}")

    dis = data.get("normalized_disease")
    if dis:
        lines.append(f"\n**Заболевание:** {dis.get('preferred_name', '?')}")
        if dis.get("synonyms"):
            lines.append(f"**Синонимы:** {', '.join(dis['synonyms'])}")
        if dis.get("subtypes"):
            lines.append(f"**Подтипы:** {', '.join(dis['subtypes'])}")

    if data.get("ambiguities"):
        lines.append("\n**Неоднозначности:**")
        for a in data["ambiguities"]:
            lines.append(f"- {a}")

    if data.get("assumptions"):
        lines.append("\n**Допущения:**")
        for a in data["assumptions"]:
            lines.append(f"- {a}")

    if data.get("missing_information"):
        lines.append("\n**Недостающая информация:**")
        for m in data["missing_information"]:
            lines.append(f"- {m}")

    if data.get("human_questions"):
        lines.append("\n**Вопросы рецензенту:**")
        for q in data["human_questions"]:
            lines.append(f"- ❓ {q}")

    completeness = data.get("completeness", "medium")
    lines.append(f"\n**Полнота:** `{completeness}`")
    if completeness == "low":
        lines.append("\n> ⚠️ Полнота LOW — критическая информация отсутствует.")

    return "\n".join(lines)


def get_pdf_hashes(run_id: str) -> list[PDFHashInfo]:
    """Get PDF hash info for a run."""
    db = _db()
    versions = db.get_pdf_versions_for_run(run_id)
    results = []
    for v in versions:
        results.append(PDFHashInfo(
            pdf_id=v["pdf_id"],
            filename=v.get("filename", v["pdf_id"]),
            sha256=v["sha256"],
            version_status=v.get("version_label", "unknown"),
        ))
    return results


# ─── Tab 2: Human Verification ───────────────────────────────────────────────

def load_verification_packet(run_id: str) -> HumanVerificationPacket | None:
    """Load enrichment packet for human review."""
    db = _db()
    orchestrator = _orchestrator(db)
    try:
        return orchestrator.build_verification_packet(run_id)
    except ValueError:
        return None


def submit_decision(
    run_id: str,
    decision: str,
    comments: str | None = None,
    corrections: dict | None = None,
) -> DecisionResult:
    """Submit human verification decision."""
    logger.info("submit_decision: run_id=%s, decision=%s", run_id, decision)

    if decision not in ("approved", "approved_with_edits", "rejected", "needs_revision"):
        return DecisionResult(success=False, error="Неверное решение.")

    # Map UI values to backend schema
    backend_decision = "approved" if decision == "approved_with_edits" else decision

    db = _db()
    orchestrator = _orchestrator(db)

    hd = HumanDecision(
        run_id=run_id,
        decision=backend_decision,  # type: ignore[arg-type]
        corrections=corrections or {},
        comments=comments,
        timestamp=_now_iso(),
    )

    try:
        record, summary = orchestrator.finalize_decision(run_id, hd)
    except ValueError as exc:
        return DecisionResult(success=False, error=str(exc))
    except Exception as exc:
        logger.exception("submit_decision failed")
        return DecisionResult(success=False, error=f"{type(exc).__name__}: {exc}")

    next_action = {
        RunStatus.completed: "Run завершён.",
        RunStatus.needs_revision: "Отправлено на доработку. Создайте новый run с исправленными данными.",
        RunStatus.human_approved: "Верификация принята. Можно запускать Scientific Agent.",
        RunStatus.failed: "Ошибка. Проверьте логи.",
    }.get(record.status, f"Статус: {record.status.value}")

    return DecisionResult(
        success=True,
        run_id=record.run_id,
        status=record.status.value,
        next_action=next_action,
    )


# ─── Tab 3: Run Progress ─────────────────────────────────────────────────────

PIPELINE_STEPS = [
    "intake_enrichment",
    "human_verification",
    "pubmed_search",
    "clinicaltrials_search",
    "fda_lookup",
    "dailymed_fallback",
    "ema_lookup",
    "pdf_retrieval",
    "evidence_normalization",
    "scientific_synthesis",
    "scientific_memo_generation",
    "market_analysis",
    "market_memo_generation",
    "obsidian_write",
]


def get_run_status(run_id: str) -> dict:
    """Get full run status including step-level detail."""
    db = _db()
    run = db.get_run(run_id)
    if run is None:
        return {"error": f"Run {run_id} не найден."}

    steps = db.get_run_steps(run_id)
    step_map = {s["step_name"]: s for s in steps}

    step_table = []
    for step_name in PIPELINE_STEPS:
        s = step_map.get(step_name)
        status = s["status"] if s else "pending"
        details = ""
        if s and s.get("details_json"):
            try:
                d = json.loads(s["details_json"])
                if d.get("warning"):
                    details = f"⚠️ {d['warning']}"
                elif d.get("error"):
                    details = f"❌ {d['error']}"
                elif d.get("results"):
                    details = f"{d['results']} results"
            except (json.JSONDecodeError, TypeError):
                pass
        step_table.append({"step": step_name, "status": status, "details": details})

    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "error": run.error_message,
        "steps": step_table,
    }


def run_scientific_agent(run_id: str) -> str:
    """Run the scientific agent for an approved run. Returns status message."""
    logger.info("run_scientific_agent: run_id=%s", run_id)

    db = _db()
    run = db.get_run(run_id)
    if run is None:
        return "❌ Run не найден."

    if run.status not in (RunStatus.human_approved, RunStatus.completed):
        if run.status == RunStatus.awaiting_human_verification:
            return "❌ Сначала пройдите верификацию (Tab 2). Scientific Agent не может запуститься без одобрения."
        if run.status in (RunStatus.human_rejected,):
            return "❌ Run отклонён. Scientific Agent не может быть запущен."
        return f"❌ Невозможно запустить Scientific Agent в статусе: {run.status.value}"

    if run.status == RunStatus.completed:
        # Check if scientific output already exists
        existing = db.get_scientific_output(run_id)
        if existing:
            return "ℹ️ Scientific Agent уже был выполнен для этого run."

    # Re-run finalize to trigger scientific stage (it's idempotent for already-approved runs)
    orchestrator = _orchestrator(db)
    enrichment = json.loads(run.enrichment_output_json or "{}")
    pdf_versions = db.get_pdf_versions_for_run(run_id)
    pdf_hashes = {v["pdf_id"]: v["sha256"] for v in pdf_versions}

    try:
        orchestrator._run_scientific_stage(run_id, enrichment, pdf_hashes)
        db.update_run_status(run_id, RunStatus.completed)
        return "✅ Scientific Agent завершён успешно."
    except Exception as exc:
        logger.exception("Scientific agent failed")
        return f"❌ Ошибка Scientific Agent: {type(exc).__name__}: {exc}"


# ─── Tab 4: Evidence Explorer ─────────────────────────────────────────────────

def get_evidence_table(run_id: str, source_filter: str = "all") -> list[dict]:
    """Load evidence sources for a run, optionally filtered."""
    db = _db()
    sources = db.get_scientific_sources(run_id)

    if source_filter and source_filter != "all":
        sources = [s for s in sources if s.get("source_type") == source_filter]

    table = []
    for s in sources:
        table.append({
            "source_id": s.get("source_id", ""),
            "source_type": s.get("source_type", ""),
            "title": (s.get("title") or "")[:80],
            "external_id": s.get("external_id", ""),
            "publication_date": s.get("publication_date", ""),
            "query_used": s.get("query_used", ""),
            "relevance": s.get("evidence_summary", "")[:60],
            "warning": s.get("reliability_notes", ""),
        })
    return table


# ─── Tab 5: Scientific Memo ──────────────────────────────────────────────────

def load_scientific_memo(run_id: str) -> tuple[str, str]:
    """Load scientific memo markdown and raw JSON output.

    Returns (memo_markdown, output_json).
    """
    db = _db()

    # Try to find memo file in vault
    memo_dir = config.vault_dir / "04_reports" / "scientific"
    memo_text = ""
    if memo_dir.exists():
        for f in sorted(memo_dir.glob(f"*{run_id}*"), reverse=True):
            if f.suffix == ".md":
                memo_text = f.read_text(encoding="utf-8")
                break

    if not memo_text:
        # Fallback: check if there's a memo in the run
        memo_text = "Научное memo ещё не сгенерировано для этого run."

    output_json = db.get_scientific_output(run_id) or "{}"
    return memo_text, output_json


# ─── Tab 6: Market Memo ──────────────────────────────────────────────────────

def load_market_memo(run_id: str) -> tuple[str, str]:
    """Load market memo markdown and raw JSON output.

    Returns (memo_markdown, output_json).
    """
    db = _db()

    memo_dir = config.vault_dir / "04_reports" / "market"
    memo_text = ""
    if memo_dir.exists():
        for f in sorted(memo_dir.glob(f"*{run_id}*"), reverse=True):
            if f.suffix == ".md":
                memo_text = f.read_text(encoding="utf-8")
                break

    if not memo_text:
        memo_text = "Market memo ещё не сгенерировано для этого run."

    output_json = db.get_market_output(run_id) or "{}"
    return memo_text, output_json


# ─── Tab 7: Logs ─────────────────────────────────────────────────────────────

def get_audit_events(run_id: str, count: int = 50, event_filter: str = "all") -> str:
    """Load audit events for a run, masked and filtered."""
    db = _db()
    events = db.get_audit_events(run_id)

    if event_filter and event_filter != "all":
        events = [e for e in events if e.get("event_type") == event_filter]

    # Take last N
    events = events[-count:]

    # Mask secrets
    raw = json.dumps(events, indent=2, ensure_ascii=False, default=str)
    return mask_secrets(raw)


# ─── Tab 7: Healthcheck ──────────────────────────────────────────────────────

def run_healthcheck() -> str:
    """Run all healthchecks and return formatted results."""
    from app.services.healthcheck import run_all_checks

    results = run_all_checks()
    lines = ["| Компонент | Статус | Детали |", "|---|---|---|"]
    for r in results:
        icon = "✅" if r.ok else ("🔴" if r.fatal else "⚠️")
        lines.append(f"| {r.name} | {icon} | {r.detail} |")

    fatals = [r for r in results if not r.ok and r.fatal]
    if fatals:
        lines.append(f"\n**🔴 Критические проблемы ({len(fatals)}):** "
                     + ", ".join(r.name for r in fatals))

    warnings = [r for r in results if not r.ok and not r.fatal]
    if warnings:
        lines.append(f"\n**⚠️ Предупреждения ({len(warnings)}):** "
                     + ", ".join(r.name for r in warnings))

    if not fatals and not warnings:
        lines.append("\n**Все компоненты в норме.**")

    return "\n".join(lines)
