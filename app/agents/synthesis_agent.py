"""Synthesis / Final Investment Memo Agent (MVP 5).

Integrates validated outputs from scientific, market, and patent/finance agents
into a single structured final assessment and Markdown report.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agents.synthesis_checks import run_all_contradiction_checks
from app.config import config
from app.llm.structured_client import StructuredLLMClient, StructuredOutputError
from app.logging.audit_logger import log_event, log_tool_call
from app.schemas.audit import AuditEvent
from app.schemas.synthesis import (
    FinalSynthesisOutput,
    SynthesisAgentInput,
    SynthesisPreconditionError,
)
from app.storage.db import Database

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_prompt(name: str) -> str:
    """Load a synthesis prompt template."""
    path = _PROMPTS_DIR / f"synthesis_agent.{name}.md"
    return path.read_text(encoding="utf-8")


def _format_pdf_hashes(hashes: dict[str, str]) -> str:
    """Format PDF hashes for the prompt."""
    if not hashes:
        return "(No PDF documents used)"
    lines = [f"- {pid}: {h[:16]}..." for pid, h in hashes.items()]
    return "\n".join(lines)


def _format_source_registry(sources: list[dict]) -> str:
    """Format source registry for the prompt."""
    if not sources:
        return "(No sources in registry)"
    
    lines = []
    for src in sources[:50]:  # Limit to avoid prompt overflow
        sid = src.get("source_id", "?")
        stype = src.get("source_type", "unknown")
        title = (src.get("title") or "")[:80]
        lines.append(f"- [{sid}] ({stype}) {title}")
    
    if len(sources) > 50:
        lines.append(f"... and {len(sources) - 50} more sources")
    
    return "\n".join(lines)


def _format_source_warnings(warnings: list[str]) -> str:
    """Format source warnings for the prompt."""
    if not warnings:
        return "(No source availability warnings)"
    return "\n".join(f"- {w}" for w in warnings)


def _format_contradictions(contradictions: list[dict]) -> str:
    """Format pre-detected contradictions for the prompt."""
    if not contradictions:
        return "(No contradictions detected by automated checks)"
    
    lines = []
    for c in contradictions:
        area = c.get("area", "unknown")
        desc = c.get("description", "")
        severity = c.get("severity", "medium")
        lines.append(f"- [{severity.upper()}] {area}: {desc}")
    
    return "\n".join(lines)


class SynthesisAgent:
    """Synthesis and QA Agent for final assessment generation.
    
    This agent:
    1. Loads validated outputs from previous stages
    2. Verifies preconditions (human approval, all stages complete)
    3. Runs deterministic contradiction checks
    4. Calls LLM with structured output
    5. Validates and persists the result
    6. Generates final Markdown report
    """

    def __init__(
        self,
        db: Database | None = None,
        client: StructuredLLMClient | None = None,
    ) -> None:
        self.db = db or Database()
        self.client = client or StructuredLLMClient()

    def run(self, run_id: str) -> FinalSynthesisOutput:
        """Execute the synthesis stage for a given run.
        
        Args:
            run_id: The run identifier.
            
        Returns:
            Validated FinalSynthesisOutput.
            
        Raises:
            SynthesisPreconditionError: If required preconditions are not met.
            StructuredOutputError: If LLM output fails validation after retry.
        """
        # Log synthesis started
        log_event(
            AuditEvent(
                event_id=f"synthesis-started-{_now_iso()}",
                run_id=run_id,
                stage="synthesis",
                event_type="stage_started",
                timestamp=_now_iso(),
                status="started",
                metadata={},
            )
        )
        
        try:
            # 1. Check preconditions
            self._check_preconditions(run_id)
            
            log_event(
                AuditEvent(
                    event_id=f"synthesis-preconditions-ok-{_now_iso()}",
                    run_id=run_id,
                    stage="synthesis",
                    event_type="tool_call",
                    timestamp=_now_iso(),
                    status="succeeded",
                    metadata={"check": "preconditions"},
                )
            )
            
            # 2. Build synthesis input
            agent_input = self._build_input(run_id)
            
            # 3. Run deterministic contradiction checks
            detected_contradictions = run_all_contradiction_checks(
                scientific_json=agent_input.scientific_output_json,
                market_json=agent_input.market_output_json,
                patent_finance_json=agent_input.patent_finance_output_json,
                source_warnings=agent_input.source_warnings,
            )
            agent_input.detected_contradictions = detected_contradictions
            
            log_tool_call(
                run_id=run_id,
                stage="synthesis",
                tool_name="contradiction_checks",
                status="succeeded",
                output_summary={"contradictions_found": len(detected_contradictions)},
            )
            
            # 4. Prepare prompt
            user_prompt = self._build_user_prompt(agent_input)
            system_prompt = _load_prompt("system")
            
            log_event(
                AuditEvent(
                    event_id=f"synthesis-prompt-prepared-{_now_iso()}",
                    run_id=run_id,
                    stage="synthesis",
                    event_type="tool_call",
                    timestamp=_now_iso(),
                    status="succeeded",
                    metadata={"prompt_length": len(user_prompt)},
                )
            )
            
            # 5. Call LLM
            log_event(
                AuditEvent(
                    event_id=f"synthesis-llm-started-{_now_iso()}",
                    run_id=run_id,
                    stage="synthesis",
                    event_type="llm_call_started",
                    timestamp=_now_iso(),
                    status="started",
                    metadata={"model": config.default_openrouter_model},
                )
            )
            
            try:
                result = self.client.call(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    output_model=FinalSynthesisOutput,
                    model=config.default_openrouter_model,
                    run_id=run_id,
                )
                output: FinalSynthesisOutput = result  # type: ignore[assignment]
                
                log_event(
                    AuditEvent(
                        event_id=f"synthesis-llm-completed-{_now_iso()}",
                        run_id=run_id,
                        stage="synthesis",
                        event_type="llm_call_completed",
                        timestamp=_now_iso(),
                        status="succeeded",
                        metadata={
                            "go_no_go": output.overall_conclusion.go_no_go_interpretation,
                            "contradictions_count": len(output.contradictions),
                        },
                    )
                )
                
            except StructuredOutputError as exc:
                log_event(
                    AuditEvent(
                        event_id=f"synthesis-llm-failed-{_now_iso()}",
                        run_id=run_id,
                        stage="synthesis",
                        event_type="llm_call_failed",
                        timestamp=_now_iso(),
                        status="failed",
                        metadata={"error": str(exc)},
                    )
                )
                # Update step status
                self.db.upsert_run_step(
                    run_id=run_id,
                    step_name="synthesis",
                    status="failed",
                    details={"error": str(exc)},
                )
                raise
            
            # 6. Post-validate source references
            self._validate_source_references(output, run_id)
            
            # 7. Save to database
            self._save_output(run_id, output)
            
            log_event(
                AuditEvent(
                    event_id=f"synthesis-output-saved-{_now_iso()}",
                    run_id=run_id,
                    stage="synthesis",
                    event_type="sqlite_persisted",
                    timestamp=_now_iso(),
                    status="succeeded",
                    metadata={},
                )
            )
            
            # 8. Write Markdown report
            report_path = self._write_report(run_id, output)
            
            log_event(
                AuditEvent(
                    event_id=f"synthesis-report-written-{_now_iso()}",
                    run_id=run_id,
                    stage="synthesis",
                    event_type="obsidian_note_written",
                    timestamp=_now_iso(),
                    status="succeeded",
                    metadata={"report_path": str(report_path)},
                )
            )
            
            # 9. Update run step
            self.db.upsert_run_step(
                run_id=run_id,
                step_name="synthesis",
                status="completed",
                details={
                    "go_no_go": output.overall_conclusion.go_no_go_interpretation,
                    "report_path": str(report_path),
                },
            )
            
            log_event(
                AuditEvent(
                    event_id=f"synthesis-completed-{_now_iso()}",
                    run_id=run_id,
                    stage="synthesis",
                    event_type="stage_completed",
                    timestamp=_now_iso(),
                    status="succeeded",
                    metadata={
                        "go_no_go": output.overall_conclusion.go_no_go_interpretation,
                        "manual_review_items": len(output.manual_review_required),
                    },
                )
            )
            
            return output
            
        except SynthesisPreconditionError:
            raise
        except Exception as exc:
            log_event(
                AuditEvent(
                    event_id=f"synthesis-failed-{_now_iso()}",
                    run_id=run_id,
                    stage="synthesis",
                    event_type="stage_failed",
                    timestamp=_now_iso(),
                    status="failed",
                    metadata={"error": str(exc), "error_type": type(exc).__name__},
                )
            )
            self.db.upsert_run_step(
                run_id=run_id,
                step_name="synthesis",
                status="failed",
                details={"error": str(exc)},
            )
            raise

    def _check_preconditions(self, run_id: str) -> None:
        """Verify all preconditions for synthesis.
        
        Raises:
            SynthesisPreconditionError: If any precondition fails.
        """
        # Check run exists
        run = self.db.get_run(run_id)
        if run is None:
            self._log_precondition_failed(run_id, "run_not_found", f"Run {run_id} not found")
            raise SynthesisPreconditionError(f"Run {run_id} not found")
        
        # Check human verification
        decision = self.db.get_human_decision(run_id)
        if decision is None:
            self._log_precondition_failed(run_id, "no_human_verification", "Human verification not found")
            raise SynthesisPreconditionError("Human verification is required before synthesis")
        
        if decision.decision not in ("approved", "approved_with_edits"):
            self._log_precondition_failed(
                run_id, "human_not_approved",
                f"Human verification status is '{decision.decision}', not approved"
            )
            raise SynthesisPreconditionError(
                f"Human verification must be approved (current: {decision.decision})"
            )
        
        # Check scientific output
        sci_output = self.db.get_scientific_output(run_id)
        if not sci_output:
            self._log_precondition_failed(run_id, "no_scientific_output", "Scientific output not found")
            raise SynthesisPreconditionError("Scientific agent output is required before synthesis")
        
        # Check market output
        mkt_output = self.db.get_market_output(run_id)
        if not mkt_output:
            self._log_precondition_failed(run_id, "no_market_output", "Market output not found")
            raise SynthesisPreconditionError("Market agent output is required before synthesis")
        
        # Check patent/finance output
        pat_output = self.db.get_patent_finance_output(run_id)
        if not pat_output:
            self._log_precondition_failed(run_id, "no_patent_finance_output", "Patent/finance output not found")
            raise SynthesisPreconditionError("Patent/finance agent output is required before synthesis")

    def _log_precondition_failed(self, run_id: str, reason: str, message: str) -> None:
        """Log a precondition failure event."""
        log_event(
            AuditEvent(
                event_id=f"synthesis-precondition-failed-{_now_iso()}",
                run_id=run_id,
                stage="synthesis",
                event_type="stage_failed",
                timestamp=_now_iso(),
                status="blocked",
                metadata={"reason": reason, "message": message},
            )
        )
        self.db.upsert_run_step(
            run_id=run_id,
            step_name="synthesis",
            status="blocked",
            details={"reason": reason, "message": message},
        )

    def _build_input(self, run_id: str) -> SynthesisAgentInput:
        """Build the input package for the synthesis agent."""
        run = self.db.get_run(run_id)
        if run is None:
            raise SynthesisPreconditionError(f"Run {run_id} not found")
        
        # Parse raw input
        raw_input = json.loads(run.raw_input_json) if run.raw_input_json else {}
        
        # Parse enrichment output for normalized values
        enrichment = {}
        if run.enrichment_output_json:
            try:
                enrichment = json.loads(run.enrichment_output_json)
            except json.JSONDecodeError:
                pass
        
        normalized_inn = enrichment.get("normalized_inn") or {}
        normalized_disease = enrichment.get("normalized_disease") or {}
        
        # Get human verification
        decision = self.db.get_human_decision(run_id)
        
        # Get PDF versions
        pdf_versions = self.db.get_pdf_versions_for_run(run_id)
        pdf_hashes = {v["pdf_id"]: v["sha256"] for v in pdf_versions}
        
        # Get agent outputs
        scientific_output = self.db.get_scientific_output(run_id)
        market_output = self.db.get_market_output(run_id)
        patent_finance_output = self.db.get_patent_finance_output(run_id)
        
        # Get source registry
        sources = self.db.get_scientific_sources(run_id)
        
        # Get source warnings from connector calls
        source_warnings = self._collect_source_warnings(run_id)
        
        # Check for PDF evidence warning
        if not pdf_versions:
            # Check if previous stages logged a warning
            steps = self.db.get_run_steps(run_id)
            pdf_warning_logged = any(
                "pdf" in (s.get("details_json") or "").lower() and "warning" in (s.get("status") or "").lower()
                for s in steps
            )
            if not pdf_warning_logged:
                source_warnings.append("PDF evidence: No PDF documents were attached to this run")
        
        return SynthesisAgentInput(
            run_id=run_id,
            inn_preferred=normalized_inn.get("preferred_name", raw_input.get("inn_raw", "")),
            inn_english=normalized_inn.get("english_inn"),
            inn_russian=normalized_inn.get("russian_name"),
            inn_synonyms=normalized_inn.get("synonyms", []),
            disease_preferred=normalized_disease.get("preferred_name", raw_input.get("disease_raw")),
            disease_synonyms=normalized_disease.get("synonyms", []),
            region=raw_input.get("region"),
            molecule_type=normalized_inn.get("molecule_type", "unknown"),
            stage=raw_input.get("stage"),
            target_patient_segment=normalized_disease.get("patient_segmentation"),
            human_verification_status=decision.decision if decision else "unknown",
            human_verification_timestamp=decision.timestamp if decision else None,
            human_verification_comments=decision.comments if decision else None,
            pdf_hashes=pdf_hashes,
            scientific_output_json=scientific_output,
            market_output_json=market_output,
            patent_finance_output_json=patent_finance_output,
            source_registry_json=json.dumps(sources, ensure_ascii=False) if sources else None,
            source_warnings=source_warnings,
        )

    def _collect_source_warnings(self, run_id: str) -> list[str]:
        """Collect source availability warnings from connector calls."""
        warnings: list[str] = []
        
        try:
            from app.storage.db import Database
            db = self.db
            
            # Check connector calls table
            with db._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT connector_name, status, errors
                    FROM scientific_connector_calls
                    WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchall()
                
                for row in rows:
                    if row["status"] in ("failed", "error", "unavailable"):
                        connector = row["connector_name"]
                        errors = row["errors"] or ""
                        warnings.append(f"{connector} unavailable: {errors[:100]}")
        except Exception:
            pass  # Connector calls table may not exist in older schemas
        
        return warnings

    def _build_user_prompt(self, agent_input: SynthesisAgentInput) -> str:
        """Build the user prompt from the input package."""
        template = _load_prompt("user")
        
        # Format scientific output
        sci_output = agent_input.scientific_output_json or "{}"
        try:
            sci_dict = json.loads(sci_output)
            sci_formatted = json.dumps(sci_dict, indent=2, ensure_ascii=False)[:8000]
        except json.JSONDecodeError:
            sci_formatted = "(Invalid JSON)"
        
        # Format market output
        mkt_output = agent_input.market_output_json or "{}"
        try:
            mkt_dict = json.loads(mkt_output)
            mkt_formatted = json.dumps(mkt_dict, indent=2, ensure_ascii=False)[:8000]
        except json.JSONDecodeError:
            mkt_formatted = "(Invalid JSON)"
        
        # Format patent/finance output
        pat_output = agent_input.patent_finance_output_json or "{}"
        try:
            pat_dict = json.loads(pat_output)
            pat_formatted = json.dumps(pat_dict, indent=2, ensure_ascii=False)[:8000]
        except json.JSONDecodeError:
            pat_formatted = "(Invalid JSON)"
        
        # Format source registry
        sources = []
        if agent_input.source_registry_json:
            try:
                sources = json.loads(agent_input.source_registry_json)
            except json.JSONDecodeError:
                pass
        
        return template.format(
            run_id=agent_input.run_id,
            inn_preferred=agent_input.inn_preferred,
            inn_english=agent_input.inn_english or "N/A",
            inn_russian=agent_input.inn_russian or "N/A",
            inn_synonyms=", ".join(agent_input.inn_synonyms) or "N/A",
            molecule_type=agent_input.molecule_type,
            disease_preferred=agent_input.disease_preferred or "N/A",
            disease_synonyms=", ".join(agent_input.disease_synonyms) or "N/A",
            region=agent_input.region or "global",
            stage=agent_input.stage or "unknown",
            target_patient_segment=agent_input.target_patient_segment or "N/A",
            human_verification_status=agent_input.human_verification_status,
            human_verification_timestamp=agent_input.human_verification_timestamp or "N/A",
            human_verification_comments=agent_input.human_verification_comments or "N/A",
            pdf_hashes=_format_pdf_hashes(agent_input.pdf_hashes),
            scientific_output=sci_formatted,
            market_output=mkt_formatted,
            patent_finance_output=pat_formatted,
            source_registry=_format_source_registry(sources),
            source_warnings=_format_source_warnings(agent_input.source_warnings),
            detected_contradictions=_format_contradictions(agent_input.detected_contradictions),
            scientific_completed="Yes" if agent_input.scientific_output_json else "No",
            market_completed="Yes" if agent_input.market_output_json else "No",
            patent_finance_completed="Yes" if agent_input.patent_finance_output_json else "No",
            total_sources=len(sources),
            source_warnings_count=len(agent_input.source_warnings),
        )

    def _validate_source_references(self, output: FinalSynthesisOutput, run_id: str) -> None:
        """Post-validate that referenced source_ids exist in the registry.
        
        Does not raise — patches output to note orphan references.
        """
        sources = self.db.get_scientific_sources(run_id)
        known_ids = {s["source_id"] for s in sources}
        
        # Collect all referenced source_ids
        all_referenced: set[str] = set()
        for section in [
            output.scientific_rationale,
            output.commercial_attractiveness,
            output.patent_and_financial_viability,
        ]:
            all_referenced.update(section.source_ids)
        
        for ref in output.source_references:
            all_referenced.add(ref.source_id)
        
        # Find orphans
        orphan_ids = all_referenced - known_ids - {""}
        if orphan_ids:
            # Add to manual review
            from app.schemas.synthesis import ManualReviewItem
            output.manual_review_required.append(
                ManualReviewItem(
                    area="source_verification",
                    reason=f"LLM referenced unknown source_ids: {', '.join(sorted(orphan_ids))}",
                    recommended_expert_type="data_analyst",
                    priority="medium",
                )
            )

    def _save_output(self, run_id: str, output: FinalSynthesisOutput) -> None:
        """Save synthesis output to database."""
        output_json = json.dumps(output.model_dump(mode="json"), ensure_ascii=False)
        self.db.save_synthesis_output(run_id, output_json)

    def _write_report(self, run_id: str, output: FinalSynthesisOutput) -> Path:
        """Write final assessment Markdown report to Obsidian vault."""
        from app.reports.final_assessment import generate_final_assessment_markdown
        
        report_path = generate_final_assessment_markdown(run_id, output)
        
        # Save report path
        self.db.save_synthesis_report_path(run_id, str(report_path))
        
        return report_path
