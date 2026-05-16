from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agents.intake_enrichment_agent import IntakeEnrichmentAgent
from app.llm.structured_client import StructuredLLMClient, StructuredOutputError
from app.config import config
from app.logging.audit_logger import log_event
from app.obsidian import writer as obsidian
from app.pdf import reader, watcher
from app.schemas.audit import AuditEvent
from app.schemas.human_decision import HumanDecision
from app.schemas.input import RawInput
from app.schemas.intake_output import HumanVerificationPacket, IntakeEnrichmentOutput
from app.schemas.pdf import PDFExtractionResult, PDFMetadata, PDFVersionStatus
from app.schemas.run import RunRecord, RunStatus, StageOutput, is_valid_transition
from app.storage.db import Database


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Orchestrator:
    """Deterministic pipeline orchestrator for MVP 1.

    The orchestrator owns:
    - Run creation and status transitions.
    - Stage ordering.
    - Persistence (SQLite, Obsidian, audit log).
    - Human gates.
    - Error handling.

    Unvalidated LLM output NEVER enters the database or Obsidian vault.
    StructuredOutputError is caught at the enrichment stage and the run
    is marked as failed before any downstream writes occur.
    """

    def __init__(
        self,
        db: Database | None = None,
        llm_client: StructuredLLMClient | None = None,
    ) -> None:
        self.db = db or Database()
        self._llm_client = llm_client

    def _get_llm_client(self) -> StructuredLLMClient:
        """Return the LLM client, creating one lazily if needed."""
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

    def run(
        self,
        raw_input: RawInput,
        pdf_paths: list[Path],
    ) -> RunRecord:
        """Execute the MVP 1 deterministic pipeline.

        Returns a RunRecord. On StructuredOutputError from the enrichment
        stage, the run is marked as failed and returned — unvalidated output
        never reaches the database or Obsidian vault.
        """
        # 1. Create run
        run = self.db.create_run(raw_input)
        self._log(run.run_id, "create_run", "state_change", "succeeded")

        # 2. Collect input (already validated by Pydantic)
        run = self.db.update_run_status(run.run_id, RunStatus.input_collected)
        self._log(run.run_id, "input_collected", "state_change", "succeeded")

        # 3. Register PDFs: hash, check status, extract text ONCE
        pdf_results = self._register_and_ingest_pdfs(run.run_id, pdf_paths)
        run = self.db.update_run_status(run.run_id, RunStatus.pdfs_registered)
        self._log(run.run_id, "pdfs_registered", "state_change", "succeeded")

        # 4. PDFs already ingested during registration (extraction happens once)
        run = self.db.update_run_status(run.run_id, RunStatus.pdfs_ingested)
        self._log(run.run_id, "pdfs_ingested", "state_change", "succeeded")

        # 5-6. Intake enrichment — the critical validation boundary
        try:
            agent = IntakeEnrichmentAgent(client=self._get_llm_client())
            enrichment = agent.run(raw_input, pdf_results, run_id=run.run_id)
            # enrichment is guaranteed to be a validated IntakeEnrichmentOutput
            self.db.save_enrichment_output(run.run_id, enrichment)
        except StructuredOutputError as exc:
            # Unvalidated output MUST NOT be saved.
            # Mark run as failed and return immediately.
            run = self.db.update_run_status(run.run_id, RunStatus.failed, error=str(exc))
            self._log(
                run.run_id,
                "intake_enrichment",
                "error",
                "failed",
                {"error": str(exc), "type": "structured_output"},
            )
            obsidian.write_run_note(run)
            return run

        run = self.db.update_run_status(run.run_id, RunStatus.intake_enriched)
        self._log(run.run_id, "intake_enriched", "state_change", "succeeded")

        # Write PDF source notes to Obsidian using already-extracted results
        for result in pdf_results:
            db_meta = self.db.get_pdf_by_sha256(result.sha256)
            status = "new" if db_meta is None else "unchanged"
            obsidian.write_pdf_source_note(result, status=status)

        # Write initial run note
        obsidian.write_run_note(run)

        # 7. Move to awaiting human verification
        run = self.db.update_run_status(run.run_id, RunStatus.awaiting_human_verification)
        self._log(run.run_id, "awaiting_human_verification", "state_change", "succeeded")

        return run

    def _register_and_ingest_pdfs(
        self, run_id: str, pdf_paths: list[Path]
    ) -> list[PDFExtractionResult]:
        """Register PDFs with hashes and extract text in a single pass.

        This replaces the previous two-step approach that extracted PDFs twice.
        Now extraction happens once, results are stored in the DB, and reused
        everywhere (enrichment agent, Obsidian notes).
        """
        results = []
        # Track hashes already processed in this run so duplicate files
        # (e.g. same file for source_1 and source_2) get consistent status.
        _run_seen_hashes: dict[str, PDFVersionStatus] = {}
        for pdf_path, pdf_id in zip(pdf_paths, ["source_1", "source_2"]):
            if not pdf_path.exists():
                raise FileNotFoundError(f"PDF not found: {pdf_path}")

            # Compute hash and check status
            h = watcher.compute_sha256(pdf_path)

            # If this exact hash was already processed in the current run,
            # reuse the same status (handles duplicate files in one run).
            if h in _run_seen_hashes:
                status = _run_seen_hashes[h]
            else:
                db_meta = self.db.get_pdf_by_sha256(h)
                if db_meta is not None and db_meta.pdf_id == pdf_id:
                    # Hash seen before for this same pdf_id → true unchanged
                    status = PDFVersionStatus.unchanged
                else:
                    # No hash match for this pdf_id — check if pdf_id was
                    # previously seen with a different hash
                    prev = self.db.get_pdf_by_pdf_id(pdf_id)
                    if prev is not None:
                        status = PDFVersionStatus.updated
                    else:
                        status = PDFVersionStatus.new
                _run_seen_hashes[h] = status

            # Extract text ONCE
            extraction_result = reader.extract_text_from_pdf(pdf_path, pdf_id)

            # Update page count in PDF metadata
            stat = pdf_path.stat()
            meta = PDFMetadata(
                pdf_id=pdf_id,
                filename=pdf_path.name,
                sha256=h,
                size_bytes=stat.st_size,
                page_count=extraction_result.page_count,
                modified_timestamp=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                ingested_at=_now_iso(),
                last_seen_at=_now_iso(),
            )
            self.db.register_pdf(meta)
            self.db.register_pdf_version(
                run_id=run_id, pdf_id=pdf_id, sha256=h, version_label=status.value
            )

            # Store extraction result as stage output
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
                run_id,
                "pdf_register",
                "tool_call",
                "succeeded",
                {"pdf_id": pdf_id, "sha256": h, "status": status.value, "page_count": extraction_result.page_count},
            )
            results.append(extraction_result)
        return results

    def build_verification_packet(self, run_id: str) -> HumanVerificationPacket:
        """Build the packet shown to the user during human verification."""
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

    def submit_human_decision(self, run_id: str, decision: HumanDecision) -> RunRecord:
        """Process the human decision and advance or halt the pipeline."""
        run = self.db.get_run(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found")
        if run.status not in (
            RunStatus.awaiting_human_verification,
            RunStatus.needs_revision,
        ):
            raise ValueError(f"Run not awaiting verification (status={run.status.value})")

        self.db.save_human_decision(run_id, decision)
        obsidian.write_decision_note(decision)
        self._log(
            run_id,
            "human_verification",
            "human_decision",
            "succeeded",
            {"decision": decision.decision, "has_corrections": bool(decision.corrections)},
        )

        if decision.decision == "approved":
            # Only write entity notes from validated enrichment output
            enrichment = json.loads(run.enrichment_output_json or "{}")
            if "normalized_inn" in enrichment:
                from app.schemas.input import NormalizedINN
                inn = NormalizedINN.model_validate(enrichment["normalized_inn"])
                obsidian.write_drug_entity_note(inn, run_id)
            if enrichment.get("normalized_disease"):
                from app.schemas.input import NormalizedDisease
                disease = NormalizedDisease.model_validate(enrichment["normalized_disease"])
                obsidian.write_disease_entity_note(disease, run_id)

            # Create placeholder downstream stage records
            for placeholder in ["scientific_agent", "market_agent", "patent_finance_agent", "synthesis_agent"]:
                self.db.save_stage_output(
                    StageOutput(
                        stage=placeholder,
                        run_id=run_id,
                        output_json=json.dumps(
                            {"status": "not_yet_implemented", "message": f"Stage {placeholder} is not yet implemented."},
                            ensure_ascii=False,
                        ),
                        created_at=_now_iso(),
                        metadata={},
                    )
                )

            run = self.db.update_run_status(run_id, RunStatus.human_approved)
            run = self.db.update_run_status(run_id, RunStatus.completed)
            self._log(run_id, "completed", "state_change", "succeeded")
        elif decision.decision == "rejected":
            run = self.db.update_run_status(run_id, RunStatus.human_rejected)
            run = self.db.update_run_status(run_id, RunStatus.failed)
            self._log(run_id, "failed", "state_change", "succeeded", {"reason": "human_rejected"})
        elif decision.decision == "needs_revision":
            run = self.db.update_run_status(run_id, RunStatus.needs_revision)
            # loop back to input_collected for re-enrichment
            run = self.db.update_run_status(run_id, RunStatus.input_collected)

        obsidian.write_run_note(run)
        return run