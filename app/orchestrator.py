from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.intake_enrichment_agent import IntakeEnrichmentAgent
from app.llm.structured_client import StructuredLLMClient, StructuredOutputError
from app.logging.audit_logger import log_event
from app.obsidian import writer as obsidian
from app.pdf import reader, watcher
from app.schemas.audit import AuditEvent
from app.schemas.human_decision import HumanDecision
from app.schemas.input import RawInput
from app.schemas.intake_output import HumanVerificationPacket
from app.schemas.pdf import PDFExtractionResult, PDFMetadata, PDFVersionStatus
from app.schemas.run import MVP1Summary, RunRecord, RunStatus, StageOutput
from app.storage.db import Database


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def compute_input_hash(raw_input: RawInput) -> str:
    """Deterministic SHA-256 of the canonical JSON representation of raw input."""
    canonical = json.dumps(raw_input.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Orchestrator:
    """Deterministic pipeline orchestrator for MVP 1.

    The orchestrator owns run creation, status transitions, persistence
    (SQLite, Obsidian, audit log), human gates, and error handling.

    Unvalidated LLM output NEVER enters the database or Obsidian vault.
    """

    def __init__(
        self,
        db: Database | None = None,
        llm_client: StructuredLLMClient | None = None,
    ) -> None:
        self.db = db or Database()
        self._llm_client = llm_client

    def _get_llm_client(self) -> StructuredLLMClient:
        if self._llm_client is None:
            self._llm_client = StructuredLLMClient()
        return self._llm_client

    def _log(
        self,
        run_id: str,
        stage: str,
        event_type: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        log_event(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                run_id=run_id,
                stage=stage,
                event_type=event_type,  # type: ignore[arg-type]
                timestamp=_now_iso(),
                status=status,  # type: ignore[arg-type]
                metadata=metadata or {},
            )
        )

    # ------------------------------------------------------------------
    # Legacy single-shot run (kept for backward compatibility with tests)
    # ------------------------------------------------------------------

    def run(
        self,
        raw_input: RawInput,
        pdf_paths: list[Path],
    ) -> RunRecord:
        """Execute the pipeline up to awaiting_human_verification.

        Returns a RunRecord. On StructuredOutputError the run is marked
        as failed and returned.
        """
        run, _packet = self.run_until_verification(raw_input, pdf_paths)
        return run

    # ------------------------------------------------------------------
    # Two-phase flow used by the end-to-end CLI
    # ------------------------------------------------------------------

    def run_until_verification(
        self,
        raw_input: RawInput,
        pdf_paths: list[Path],
    ) -> tuple[RunRecord, HumanVerificationPacket | None]:
        """Phase 1: create run -> hash -> PDFs -> enrich -> await verification.

        Returns (run_record, verification_packet).  If enrichment fails,
        the run is marked failed and packet is None.
        """
        # 1. Compute input hash
        input_hash = compute_input_hash(raw_input)

        # 2. Create run
        run = self.db.create_run(raw_input, input_hash=input_hash)
        self._log(run.run_id, "create_run", "state_change", "succeeded",
                  {"input_hash": input_hash})

        # 3. Input collected
        run = self.db.update_run_status(run.run_id, RunStatus.input_collected)
        self._log(run.run_id, "input_collected", "state_change", "succeeded")

        # 4. Register and extract PDFs
        pdf_results = self._register_and_ingest_pdfs(run.run_id, pdf_paths)
        run = self.db.update_run_status(run.run_id, RunStatus.pdfs_registered)
        self._log(run.run_id, "pdfs_registered", "state_change", "succeeded")

        run = self.db.update_run_status(run.run_id, RunStatus.pdfs_ingested)
        self._log(run.run_id, "pdfs_ingested", "state_change", "succeeded")

        # 5-6. Intake enrichment
        try:
            agent = IntakeEnrichmentAgent(client=self._get_llm_client())
            enrichment = agent.run(raw_input, pdf_results, run_id=run.run_id)
            self.db.save_enrichment_output(run.run_id, enrichment)
        except StructuredOutputError as exc:
            run = self.db.update_run_status(run.run_id, RunStatus.failed, error=str(exc))
            self._log(run.run_id, "intake_enrichment", "error", "failed",
                      {"error": str(exc), "type": "structured_output"})
            obsidian.write_run_note(run)
            return run, None

        run = self.db.update_run_status(run.run_id, RunStatus.intake_enriched)
        self._log(run.run_id, "intake_enriched", "state_change", "succeeded")

        # Write PDF source notes
        for result in pdf_results:
            db_meta = self.db.get_pdf_by_sha256(result.sha256)
            status = "new" if db_meta is None else "unchanged"
            obsidian.write_pdf_source_note(result, status=status)

        obsidian.write_run_note(run)

        # 7. Move to awaiting verification
        run = self.db.update_run_status(run.run_id, RunStatus.awaiting_human_verification)
        self._log(run.run_id, "awaiting_human_verification", "state_change", "succeeded")

        packet = self.build_verification_packet(run.run_id)
        return run, packet

    def finalize_decision(self, run_id: str, decision: HumanDecision) -> tuple[RunRecord, MVP1Summary | None]:
        """Phase 2: process human decision and finalize the run.

        Returns (run_record, summary_or_None).
        """
        run = self.db.get_run(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found")
        if run.status not in (RunStatus.awaiting_human_verification, RunStatus.needs_revision):
            raise ValueError(f"Run not awaiting verification (status={run.status.value})")

        self.db.save_human_decision(run_id, decision)
        obsidian.write_decision_note(decision)
        self._log(run_id, "human_verification", "human_decision", "succeeded",
                  {"decision": decision.decision, "has_corrections": bool(decision.corrections)})

        if decision.decision == "approved":
            return self._finalize_approved(run_id, run)
        elif decision.decision == "rejected":
            return self._finalize_rejected(run_id)
        else:
            run = self.db.update_run_status(run_id, RunStatus.needs_revision)
            run = self.db.update_run_status(run_id, RunStatus.input_collected)
            return run, None

    def _finalize_approved(self, run_id: str, run: RunRecord) -> tuple[RunRecord, MVP1Summary]:
        """Write entity notes, build MVP1 summary, and complete the run."""
        enrichment = json.loads(run.enrichment_output_json or "{}")

        if "normalized_inn" in enrichment:
            from app.schemas.input import NormalizedINN
            inn = NormalizedINN.model_validate(enrichment["normalized_inn"])
            obsidian.write_drug_entity_note(inn, run_id)
        if enrichment.get("normalized_disease"):
            from app.schemas.input import NormalizedDisease
            disease = NormalizedDisease.model_validate(enrichment["normalized_disease"])
            obsidian.write_disease_entity_note(disease, run_id)

        # Build final MVP 1 summary
        pdf_versions = self.db.get_pdf_versions_for_run(run_id)
        pdf_hashes = {v["pdf_id"]: v["sha256"] for v in pdf_versions}
        inn_data = enrichment.get("normalized_inn", {})

        summary = MVP1Summary(
            run_id=run_id,
            inn_preferred=inn_data.get("preferred_name", ""),
            inn_english=inn_data.get("english_inn"),
            inn_russian=inn_data.get("russian_name"),
            disease_preferred=(
                enrichment.get("normalized_disease", {}).get("preferred_name")
                if enrichment.get("normalized_disease")
                else None
            ),
            input_hash=run.input_hash or "",
            pdf_hashes=pdf_hashes,
            enrichment_completeness=enrichment.get("completeness", "medium"),
            human_decision="approved",
        )
        summary_json = json.dumps(summary.model_dump(mode="json"), ensure_ascii=False)
        self.db.save_final_summary(run_id, summary_json)

        run = self.db.update_run_status(run_id, RunStatus.human_approved)
        run = self.db.update_run_status(run_id, RunStatus.completed)
        self._log(run_id, "completed", "state_change", "succeeded")

        obsidian.write_run_note(run)
        return run, summary

    def _finalize_rejected(self, run_id: str) -> tuple[RunRecord, None]:
        """Persist rejection as a first-class business outcome."""
        run = self.db.update_run_status(run_id, RunStatus.human_rejected)
        run = self.db.update_run_status(run_id, RunStatus.completed)
        self._log(run_id, "completed", "state_change", "succeeded",
                  {"reason": "human_rejected"})
        obsidian.write_run_note(run)
        return run, None

    # Keep backward-compatible aliases used by existing tests
    def submit_human_decision(self, run_id: str, decision: HumanDecision) -> RunRecord:
        run, _ = self.finalize_decision(run_id, decision)
        return run

    # ------------------------------------------------------------------
    # PDF registration
    # ------------------------------------------------------------------

    def _register_and_ingest_pdfs(
        self, run_id: str, pdf_paths: list[Path]
    ) -> list[PDFExtractionResult]:
        results = []
        _run_seen_hashes: dict[str, PDFVersionStatus] = {}
        for pdf_path, pdf_id in zip(pdf_paths, ["source_1", "source_2"], strict=False):
            if not pdf_path.exists():
                raise FileNotFoundError(f"PDF not found: {pdf_path}")

            h = watcher.compute_sha256(pdf_path)

            if h in _run_seen_hashes:
                status = _run_seen_hashes[h]
            else:
                db_meta = self.db.get_pdf_by_sha256(h)
                if db_meta is not None and db_meta.pdf_id == pdf_id:
                    status = PDFVersionStatus.unchanged
                else:
                    prev = self.db.get_pdf_by_pdf_id(pdf_id)
                    status = PDFVersionStatus.updated if prev is not None else PDFVersionStatus.new
                _run_seen_hashes[h] = status

            extraction_result = reader.extract_text_from_pdf(pdf_path, pdf_id)

            stat = pdf_path.stat()
            meta = PDFMetadata(
                pdf_id=pdf_id,
                filename=pdf_path.name,
                sha256=h,
                size_bytes=stat.st_size,
                page_count=extraction_result.page_count,
                modified_timestamp=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                ingested_at=_now_iso(),
                last_seen_at=_now_iso(),
            )
            self.db.register_pdf(meta)
            self.db.register_pdf_version(
                run_id=run_id, pdf_id=pdf_id, sha256=h, version_label=status.value
            )

            self.db.save_stage_output(
                StageOutput(
                    stage="pdf_extraction",
                    run_id=run_id,
                    output_json=extraction_result.model_dump_json(),
                    created_at=_now_iso(),
                    metadata={"pdf_id": pdf_id, "sha256": h, "page_count": extraction_result.page_count},
                )
            )
            self.db.upsert_run_step(
                run_id=run_id,
                step_name=pdf_id,
                status="extracted",
                details={"sha256": h, "pages": extraction_result.page_count, "version_status": status.value},
            )

            self._log(
                run_id, "pdf_register", "tool_call", "succeeded",
                {"pdf_id": pdf_id, "sha256": h, "status": status.value, "page_count": extraction_result.page_count},
            )
            results.append(extraction_result)
        return results

    # ------------------------------------------------------------------
    # Verification packet
    # ------------------------------------------------------------------

    def build_verification_packet(self, run_id: str) -> HumanVerificationPacket:
        run = self.db.get_run(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found")
        if run.status != RunStatus.awaiting_human_verification:
            raise ValueError(f"Run not awaiting verification (status={run.status.value})")

        raw = json.loads(run.raw_input_json)
        enrichment = json.loads(run.enrichment_output_json or "{}")
        pdf_outputs = self.db.get_stage_outputs(run_id)
        pdf_status = {}
        for out in pdf_outputs:
            if out.stage == "pdf_extraction":
                meta = json.loads(out.output_json)
                pdf_status[meta["pdf_id"]] = "extracted"

        return HumanVerificationPacket(
            run_id=run_id,
            raw_inn=raw["inn_raw"],
            raw_disease=raw.get("disease_raw"),
            normalized_inn=enrichment.get("normalized_inn", {}),
            normalized_disease=enrichment.get("normalized_disease") if enrichment.get("normalized_disease") else None,
            ambiguities=enrichment.get("ambiguities", []),
            assumptions=enrichment.get("assumptions", []),
            missing_information=enrichment.get("missing_information", []),
            questions=enrichment.get("human_questions", []),
            pdf_extraction_status=pdf_status,
            completeness=enrichment.get("completeness", "medium"),
        )
