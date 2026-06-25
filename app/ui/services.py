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


_NETWORK_KEYWORDS = (
    "RemoteProtocolError", "ReadError", "ConnectError",
    "ReadTimeout", "ConnectTimeout", "PoolTimeout",
    "network error", "peer closed", "incomplete chunked",
    "Сетевая ошибка", "сервер разорвал",
)


def _is_network_error(text: str) -> bool:
    """Check if an error message indicates a network-level failure."""
    return any(kw in text for kw in _NETWORK_KEYWORDS)


def _format_user_error(exc: Exception) -> str:
    """Format an exception into a user-friendly error message (Russian)."""
    exc_str = str(exc)
    if _is_network_error(exc_str):
        return (
            "⚠️ Ошибка сети при обращении к LLM-серверу. "
            "Сервер разорвал соединение до завершения ответа. "
            "Это может быть вызвано большим объёмом генерируемого текста "
            "или временной нестабильностью сети. "
            "Попробуйте повторить запуск через несколько минут."
        )
    # Mask secrets before exposing to UI
    safe_msg = mask_secrets(exc_str)
    return f"Ошибка: {type(exc).__name__}: {safe_msg[:300]}"


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
        return RunCreateResult(success=False, error="INN is required.")

    if not pdf1_path or not pdf2_path:
        return RunCreateResult(success=False, error="You must upload exactly 2 PDF files.")

    pdf1 = Path(pdf1_path)
    pdf2 = Path(pdf2_path)

    # Copy uploaded PDFs to configured pdfs dir
    target_dir = config.pdfs_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    pdf_paths: list[Path] = []
    for src in (pdf1, pdf2):
        if not src.exists():
            return RunCreateResult(success=False, error=f"PDF file not found: {src.name}")
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
            error=record.error_message or "Unknown error during enrichment.",
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
        return "Error parsing enrichment JSON."

    lines: list[str] = ["## Enrichment Result\n"]
    inn = data.get("normalized_inn", {})
    if inn:
        lines.append(f"**INN (preferred):** {inn.get('preferred_name', '?')}")
        if inn.get("english_inn"):
            lines.append(f"**English INN:** {inn['english_inn']}")
        if inn.get("russian_name"):
            lines.append(f"**Russian INN:** {inn['russian_name']}")
        if inn.get("synonyms"):
            lines.append(f"**Synonyms:** {', '.join(inn['synonyms'])}")
        if inn.get("brand_names"):
            lines.append(f"**Brands:** {', '.join(inn['brand_names'])}")
        lines.append(f"**Molecule type:** {inn.get('molecule_type', '?')}")
        lines.append(f"**Confidence:** {inn.get('confidence', '?')}")

    dis = data.get("normalized_disease")
    if dis:
        lines.append(f"\n**Disease:** {dis.get('preferred_name', '?')}")
        if dis.get("synonyms"):
            lines.append(f"**Synonyms:** {', '.join(dis['synonyms'])}")
        if dis.get("subtypes"):
            lines.append(f"**Subtypes:** {', '.join(dis['subtypes'])}")

    if data.get("ambiguities"):
        lines.append("\n**Ambiguities:**")
        for a in data["ambiguities"]:
            lines.append(f"- {a}")

    if data.get("assumptions"):
        lines.append("\n**Assumptions:**")
        for a in data["assumptions"]:
            lines.append(f"- {a}")

    if data.get("missing_information"):
        lines.append("\n**Missing information:**")
        for m in data["missing_information"]:
            lines.append(f"- {m}")

    if data.get("human_questions"):
        lines.append("\n**Questions for reviewer:**")
        for q in data["human_questions"]:
            lines.append(f"- ❓ {q}")

    completeness = data.get("completeness", "medium")
    lines.append(f"\n**Completeness:** `{completeness}`")
    if completeness == "low":
        lines.append("\n> ⚠️ Completeness LOW — critical information is missing.")

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
        return DecisionResult(success=False, error="Invalid decision.")

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
        error_msg = _format_user_error(exc)
        return DecisionResult(success=False, error=error_msg)

    # Build user-friendly next action message
    if record.status == RunStatus.failed:
        error_detail = record.error_message or "Unknown error"
        if _is_network_error(error_detail):
            next_action = (
                "⚠️ Ошибка сети при обращении к LLM. "
                "Попробуйте повторить запуск через несколько минут."
            )
        else:
            next_action = f"❌ Ошибка при выполнении анализа: {error_detail[:150]}"
    else:
        next_action = {
            RunStatus.completed: "✅ Все этапы анализа завершены успешно.",
            RunStatus.needs_revision: "Sent for revision. Create a new run with corrected data.",
            RunStatus.human_approved: "Verification accepted. You can now run Scientific Agent.",
        }.get(record.status, f"Status: {record.status.value}")

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
        return {"error": f"Run {run_id} not found."}

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
        return "❌ Run not found."

    if run.status not in (RunStatus.human_approved, RunStatus.completed):
        if run.status == RunStatus.awaiting_human_verification:
            return "❌ Complete verification first (Tab 2). Scientific Agent cannot run without approval."
        if run.status in (RunStatus.human_rejected,):
            return "❌ Run rejected. Scientific Agent cannot run."
        return f"❌ Cannot run Scientific Agent in status: {run.status.value}"

    if run.status == RunStatus.completed:
        # Check if scientific output already exists
        existing = db.get_scientific_output(run_id)
        if existing:
            return "ℹ️ Scientific Agent has already run for this run."

    # Re-run finalize to trigger scientific stage (it's idempotent for already-approved runs)
    orchestrator = _orchestrator(db)
    enrichment = json.loads(run.enrichment_output_json or "{}")
    pdf_versions = db.get_pdf_versions_for_run(run_id)
    pdf_hashes = {v["pdf_id"]: v["sha256"] for v in pdf_versions}

    try:
        orchestrator._run_scientific_stage(run_id, enrichment, pdf_hashes)
        db.update_run_status(run_id, RunStatus.completed)
        return "✅ Scientific Agent completed successfully."
    except Exception as exc:
        logger.exception("Scientific agent failed")
        return f"❌ Scientific Agent error: {type(exc).__name__}: {exc}"


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
        memo_text = "Scientific memo has not been generated for this run yet."

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
        memo_text = "Market memo has not been generated for this run yet."

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


# ─── Tab 9: Healthcheck ──────────────────────────────────────────────────────

def run_healthcheck() -> str:
    """Run all healthchecks and return formatted results."""
    from app.services.healthcheck import run_all_checks

    results = run_all_checks()
    lines = ["| Component | Status | Details |", "|---|---|---|"]
    for r in results:
        icon = "✅" if r.ok else ("🔴" if r.fatal else "⚠️")
        lines.append(f"| {r.name} | {icon} | {r.detail} |")

    fatals = [r for r in results if not r.ok and r.fatal]
    if fatals:
        lines.append(f"\n**🔴 Critical issues ({len(fatals)}):** "
                     + ", ".join(r.name for r in fatals))

    warnings = [r for r in results if not r.ok and not r.fatal]
    if warnings:
        lines.append(f"\n**⚠️ Warnings ({len(warnings)}):** "
                     + ", ".join(r.name for r in warnings))

    if not fatals and not warnings:
        lines.append("\n**All components are healthy.**")

    return "\n".join(lines)


# ─── Tab 7: Final Synthesis ──────────────────────────────────────────────────

def run_synthesis_agent(run_id: str) -> dict:
    """Run the final synthesis agent for a given run_id."""
    logger.info("run_synthesis_agent: run_id=%r", run_id)
    
    run_id = (run_id or "").strip()
    if not run_id:
        return {"success": False, "error": "Run ID is required."}
    
    db = _db()
    
    try:
        from app.agents.synthesis_agent import SynthesisAgent
        from app.schemas.synthesis import SynthesisPreconditionError
        from app.llm.structured_client import StructuredOutputError
        
        agent = SynthesisAgent(db=db)
        output = agent.run(run_id)
        
        # Read full report from vault
        report_preview = ""
        report_dir = config.vault_dir / "04_reports" / "final"
        if report_dir.exists():
            for f in sorted(report_dir.glob(f"*{run_id}*"), reverse=True):
                if f.suffix == ".md":
                    content = f.read_text(encoding="utf-8")
                    report_preview = content
                    break
        
        # Format contradictions for UI
        contradictions = []
        for c in output.contradictions:
            contradictions.append({
                "area": c.area,
                "description": c.description,
                "severity": c.severity,
                "affected_conclusion": c.affected_conclusion,
            })
        
        # Format manual review items for UI
        manual_review_items = []
        for item in output.manual_review_required:
            manual_review_items.append({
                "area": item.area,
                "reason": item.reason,
                "recommended_expert_type": item.recommended_expert_type,
                "priority": item.priority,
            })
        
        return {
            "success": True,
            "run_id": run_id,
            "go_no_go": output.overall_conclusion.go_no_go_interpretation,
            "main_reason": output.overall_conclusion.main_reason,
            "summary": output.overall_conclusion.summary,
            "contradictions_count": len(output.contradictions),
            "manual_review_count": len(output.manual_review_required),
            "contradictions": contradictions,
            "manual_review_items": manual_review_items,
            "report_preview": report_preview,
            "output_json": output.model_dump(mode="json"),
        }
        
    except SynthesisPreconditionError as e:
        logger.warning("Synthesis precondition failed: %s", e)
        return {"success": False, "error": f"Preconditions not met: {e}"}
        
    except StructuredOutputError as e:
        logger.error("Synthesis LLM output validation failed: %s", e)
        return {"success": False, "error": f"LLM validation error: {e}"}
        
    except Exception as e:
        logger.exception("Synthesis agent error")
        return {"success": False, "error": f"Synthesis error: {e}"}


def load_synthesis_report(run_id: str) -> dict:
    """Load existing synthesis report for a given run_id."""
    logger.info("load_synthesis_report: run_id=%r", run_id)
    
    run_id = (run_id or "").strip()
    if not run_id:
        return {"success": False, "error": "Run ID is required."}
    
    db = _db()
    
    # Try to load from database
    output_json = db.get_synthesis_output(run_id)
    if not output_json:
        return {"success": False, "error": "Synthesis report not found for this run."}
    
    try:
        output_data = json.loads(output_json)
    except json.JSONDecodeError:
        return {"success": False, "error": "Unable to parse report JSON."}
    
    # Try to load markdown report
    report_content = ""
    report_dir = config.vault_dir / "04_reports" / "final"
    if report_dir.exists():
        for f in sorted(report_dir.glob(f"*{run_id}*"), reverse=True):
            if f.suffix == ".md":
                report_content = f.read_text(encoding="utf-8")
                break
    
    if not report_content:
        report_content = "*Markdown report not found.*"
    
    # Extract key fields
    overall = output_data.get("overall_conclusion", {})
    
    return {
        "success": True,
        "run_id": run_id,
        "go_no_go": overall.get("go_no_go_interpretation", "?"),
        "main_reason": overall.get("main_reason", "N/A"),
        "summary": overall.get("summary", ""),
        "report_content": report_content,
        "output_json": output_data,
    }
