from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.intake_enrichment_agent import IntakeEnrichmentAgent
from app.agents.market_agent import MarketAgent
from app.agents.patent_finance_agent import PatentFinanceAgent
from app.agents.scientific_agent import ScientificAgent
from app.config import config
from app.connectors.clinicaltrials import ClinicalTrialsConnector
from app.connectors.ema import EMAConnector
from app.connectors.epo_ops import EPOOPSConnector
from app.connectors.fda import FDAConnector
from app.connectors.orange_book import OrangeBookConnector
from app.connectors.patent_aggregator import PatentAggregator
from app.connectors.purple_book import PurpleBookConnector
from app.connectors.pubmed import PubMedConnector
from app.connectors.uspto import USPTOConnector
from app.connectors.wipo import WIPOConnector
from app.schemas.ru_patent import PatentQuery
from app.evidence.citations import build_citation_list
from app.evidence.normalization import compute_connector_coverage, merge_connector_results
from app.evidence.ranking import rank_evidence
from app.llm.structured_client import StructuredLLMClient, StructuredOutputError
from app.logging.audit_logger import log_event
from app.obsidian import writer as obsidian
from app.pdf import reader, watcher
from app.pdf.retrieval import retrieve_pdf_evidence
from app.schemas.audit import AuditEvent
from app.schemas.evidence import ConnectorQuery, ConnectorResult
from app.schemas.human_decision import HumanDecision
from app.schemas.input import RawInput
from app.schemas.intake_output import HumanVerificationPacket
from app.schemas.market import MarketAgentInput, MarketAgentOutput
from app.schemas.pdf import PDFExtractionResult, PDFMetadata, PDFVersionStatus
from app.schemas.run import MVP1Summary, RunRecord, RunStatus, StageOutput
from app.schemas.scientific import ScientificAgentInput, ScientificAgentOutput
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

        # 5-6. Intake enrichment (PDF not used - analyzed in later stages)
        try:
            agent = IntakeEnrichmentAgent(client=self._get_llm_client())
            enrichment = agent.run(raw_input, run_id=run.run_id)
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
            # needs_revision: record the decision, reset to needs_revision status
            run = self.db.update_run_status(run_id, RunStatus.needs_revision)
            self._log(run_id, "needs_revision", "state_change", "succeeded",
                      {"corrections": decision.corrections, "comments": decision.comments})
            obsidian.write_run_note(run)
            return run, None

    def _finalize_approved(self, run_id: str, run: RunRecord) -> tuple[RunRecord, MVP1Summary]:
        """Write entity notes, build MVP1 summary, run scientific stage, and complete."""
        enrichment = json.loads(run.enrichment_output_json or "{}")

        if "normalized_inn" in enrichment:
            from app.schemas.input import NormalizedINN
            inn = NormalizedINN.model_validate(enrichment["normalized_inn"])
            obsidian.write_drug_entity_note(inn, run_id)
        if enrichment.get("normalized_disease"):
            from app.schemas.input import NormalizedDisease
            disease = NormalizedDisease.model_validate(enrichment["normalized_disease"])
            obsidian.write_disease_entity_note(disease, run_id)

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
        self._log(run_id, "human_approved", "state_change", "succeeded")

        # Register placeholder downstream stages for future pipeline extensions
        _now = _now_iso()
        for stage_name in ("scientific_analysis", "market_analysis", "patent_finance_analysis", "synthesis_qa"):
            self.db.save_stage_output(
                StageOutput(
                    stage=f"{stage_name}_placeholder",
                    run_id=run_id,
                    output_json=json.dumps({
                        "status": "not_yet_implemented" if stage_name != "scientific_analysis" else "pending",
                        "expected_inputs": ["human_approved_normalized_input", "pdf_evidence"],
                        "expected_outputs": [f"{stage_name}_structured_output"],
                        "depends_on": "human_approved_intake",
                    }),
                    created_at=_now,
                    metadata={"placeholder": stage_name != "scientific_analysis"},
                )
            )

        # MVP2: run scientific analysis stage
        logging.getLogger("pharm_agent.orchestrator").info(
            "[%s] Starting scientific analysis stage...", run_id
        )
        try:
            self._run_scientific_stage(run_id, enrichment, pdf_hashes)
        except (StructuredOutputError, Exception) as exc:
            run = self.db.update_run_status(run_id, RunStatus.failed, error=str(exc))
            self._log(run_id, "scientific_analysis", "error", "failed",
                      {"error": str(exc)})
            obsidian.write_run_note(run)
            return run, summary

        # MVP3: run market analysis stage
        logging.getLogger("pharm_agent.orchestrator").info(
            "[%s] Starting market analysis stage...", run_id
        )
        try:
            self._run_market_stage(run_id, enrichment, pdf_hashes)
        except (StructuredOutputError, Exception) as exc:
            run = self.db.update_run_status(run_id, RunStatus.failed, error=str(exc))
            self._log(run_id, "market_analysis", "error", "failed",
                      {"error": str(exc)})
            obsidian.write_run_note(run)
            return run, summary

        # Gate 2 placeholder: in future MVPs, scientific + market conclusions
        # will be presented to the human for verification before proceeding
        # to patent/finance analysis. For now, we log and continue.
        self._log(run_id, "gate_2_placeholder", "state_change", "succeeded",
                  {"note": "Gate 2 (scientific+market review before patent/finance) not yet implemented"})

        # MVP4: run patent/finance analysis stage
        logging.getLogger("pharm_agent.orchestrator").info(
            "[%s] Starting patent/finance analysis stage...", run_id
        )
        try:
            self._run_patent_finance_stage(run_id, enrichment, pdf_hashes)
        except (StructuredOutputError, Exception) as exc:
            run = self.db.update_run_status(run_id, RunStatus.failed, error=str(exc))
            self._log(run_id, "patent_finance_analysis", "error", "failed",
                      {"error": str(exc)})
            obsidian.write_run_note(run)
            return run, summary

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

    # ------------------------------------------------------------------
    # MVP2: Scientific analysis stage
    # ------------------------------------------------------------------

    def _run_scientific_stage(
        self,
        run_id: str,
        enrichment: dict[str, Any],
        pdf_hashes: dict[str, str],
    ) -> ScientificAgentOutput:
        """Collect evidence from all connectors, rank, call LLM, persist, write memo."""
        inn_data = enrichment.get("normalized_inn", {})
        disease_data = enrichment.get("normalized_disease") or {}

        query = ConnectorQuery(
            inn=inn_data.get("preferred_name", ""),
            disease=disease_data.get("preferred_name"),
            synonyms=inn_data.get("synonyms", []),
            brand_names=inn_data.get("brand_names", []),
            mesh_terms=disease_data.get("mesh", []),
            max_results=20,
        )

        # 1. Collect evidence from all connectors
        connector_results: list[ConnectorResult] = []

        pdf_outputs = self.db.get_stage_outputs(run_id)
        pdf_result = retrieve_pdf_evidence(pdf_outputs, query)
        connector_results.append(pdf_result)

        for ConnectorClass in (PubMedConnector, ClinicalTrialsConnector, FDAConnector, EMAConnector):
            try:
                connector = ConnectorClass()
                result = connector.search(query, run_id=run_id)
                connector_results.append(result)
            except Exception as exc:
                logging.getLogger("pharm_agent.orchestrator").warning(
                    "[%s] Connector %s crashed: %s", run_id, ConnectorClass.connector_name, exc,
                )
                connector_results.append(ConnectorResult(
                    connector_name=ConnectorClass.connector_name,
                    query=query,
                    errors=[f"{type(exc).__name__}: {exc}"],
                ))

        # 2. Normalize and rank
        all_sources, all_evidence = merge_connector_results(connector_results)
        ranked_evidence = rank_evidence(all_evidence)
        coverage = compute_connector_coverage(connector_results)

        _log = logging.getLogger("pharm_agent.orchestrator")
        _log.info(
            "[%s] Evidence collected: %d sources, %d evidence items. Coverage: %s",
            run_id, len(all_sources), len(ranked_evidence), coverage,
        )
        for cr in connector_results:
            if cr.errors:
                _log.warning("[%s] %s errors: %s", run_id, cr.connector_name, cr.errors)
            if cr.warnings:
                _log.info("[%s] %s warnings: %s", run_id, cr.connector_name, cr.warnings)

        run = self.db.update_run_status(run_id, RunStatus.scientific_evidence_collected)
        self._log(run_id, "scientific_evidence_collected", "state_change", "succeeded",
                  {"sources_count": len(all_sources), "evidence_count": len(ranked_evidence)})

        # 3. Persist sources and evidence
        for src in all_sources:
            self.db.save_scientific_source(run_id, src.model_dump(mode="json"))
        for evi in ranked_evidence:
            self.db.save_scientific_evidence_item(run_id, evi.model_dump(mode="json"))

        for cr in connector_results:
            self.db.save_scientific_connector_call({
                "run_id": run_id,
                "connector_name": cr.connector_name,
                "query_json": cr.query.model_dump_json(),
                "status": "failed" if cr.errors and not cr.sources else ("partial" if cr.errors else "succeeded"),
                "results_returned": cr.results_returned,
                "errors": cr.errors,
                "duration_ms": cr.duration_ms,
                "timestamp": _now_iso(),
            })

        # 4. Build scientific agent input
        agent_input = ScientificAgentInput(
            run_id=run_id,
            inn_preferred=inn_data.get("preferred_name", ""),
            inn_english=inn_data.get("english_inn"),
            inn_synonyms=inn_data.get("synonyms", []),
            disease_preferred=disease_data.get("preferred_name"),
            disease_synonyms=disease_data.get("synonyms", []),
            region=enrichment.get("region"),
            pdf_hashes=pdf_hashes,
            evidence_items_json=json.dumps(
                [e.model_dump(mode="json") for e in ranked_evidence], ensure_ascii=False
            ),
            sources_json=json.dumps(
                [s.model_dump(mode="json") for s in all_sources], ensure_ascii=False
            ),
            connector_coverage=coverage,
        )

        # 5. Call scientific agent LLM
        agent = ScientificAgent(client=self._get_llm_client())
        output = agent.run(agent_input, all_sources, ranked_evidence)

        # 6. Persist scientific output
        output_json = json.dumps(output.model_dump(mode="json"), ensure_ascii=False)
        self.db.save_scientific_output(run_id, output_json)

        run = self.db.update_run_status(run_id, RunStatus.scientific_analyzed)
        self._log(run_id, "scientific_analyzed", "state_change", "succeeded")

        # 7. Write scientific memo and source notes to Obsidian
        memo_path = obsidian.write_scientific_memo(
            run_id=run_id,
            output=output,
            sources=all_sources,
            coverage=coverage,
            pdf_hashes=pdf_hashes,
        )
        for src in all_sources:
            obsidian.write_source_note(src)
        import hashlib as _hl
        memo_text = memo_path.read_text(encoding="utf-8")
        self.db.save_scientific_memo_version(
            run_id, str(memo_path), _hl.sha256(memo_text.encode()).hexdigest()[:16],
        )

        return output

    def _run_market_stage(
        self,
        run_id: str,
        enrichment: dict[str, Any],
        pdf_hashes: dict[str, str],
    ) -> MarketAgentOutput:
        """Run market attractiveness analysis using scientific stage outputs."""
        inn_data = enrichment.get("normalized_inn", {})
        disease_data = enrichment.get("normalized_disease") or {}

        # Load scientific output from DB for context
        scientific_json = self.db.get_scientific_output(run_id)
        sci_output: dict[str, Any] = {}
        if scientific_json:
            import contextlib
            with contextlib.suppress(json.JSONDecodeError):
                sci_output = json.loads(scientific_json)

        # Reuse evidence already collected in the scientific stage
        stored_sources = self.db.get_scientific_sources(run_id)
        stored_evidence = self.db.get_scientific_evidence_items(run_id)

        from app.schemas.evidence import EvidenceItem, SourceRecord
        all_sources = [SourceRecord.model_validate(s) for s in stored_sources]
        all_evidence = []
        for e_row in stored_evidence:
            if isinstance(e_row.get("key_findings"), str):
                e_row["key_findings"] = json.loads(e_row["key_findings"])
            all_evidence.append(EvidenceItem.model_validate(e_row))

        # Build market agent input
        approved_therapies_raw = sci_output.get("approved_therapies", [])
        pipeline_raw = sci_output.get("clinical_trial_landscape", [])

        agent_input = MarketAgentInput(
            run_id=run_id,
            inn_preferred=inn_data.get("preferred_name", ""),
            inn_english=inn_data.get("english_inn"),
            inn_synonyms=inn_data.get("synonyms", []),
            disease_preferred=disease_data.get("preferred_name"),
            disease_synonyms=disease_data.get("synonyms", []),
            region=enrichment.get("region"),
            molecule_type=inn_data.get("molecule_type", "unknown"),
            stage=enrichment.get("stage"),
            scientific_summary=sci_output.get("executive_summary", ""),
            approved_therapies_json=json.dumps(approved_therapies_raw, ensure_ascii=False)
            if approved_therapies_raw else None,
            clinical_pipeline_json=json.dumps(pipeline_raw, ensure_ascii=False)
            if pipeline_raw else None,
            unmet_need=(
                sci_output.get("unmet_medical_need", {}).get("text")
                if isinstance(sci_output.get("unmet_medical_need"), dict)
                else sci_output.get("unmet_medical_need")
            ),
            evidence_items_json=json.dumps(
                [e.model_dump(mode="json") for e in all_evidence], ensure_ascii=False
            ),
            sources_json=json.dumps(
                [s.model_dump(mode="json") for s in all_sources], ensure_ascii=False
            ),
            pdf_hashes=pdf_hashes,
        )

        # Call market agent
        agent = MarketAgent(client=self._get_llm_client())
        output = agent.run(agent_input, all_sources, all_evidence)

        # Persist market output
        output_json = json.dumps(output.model_dump(mode="json"), ensure_ascii=False)
        self.db.save_stage_output(StageOutput(
            run_id=run_id,
            stage="market_analysis",
            output_json=output_json,
            status="succeeded",
            created_at=_now_iso(),
        ))

        self.db.update_run_status(run_id, RunStatus.market_analyzed)
        self._log(run_id, "market_analyzed", "state_change", "succeeded")

        # Write market memo to Obsidian
        memo_path = obsidian.write_market_memo(
            run_id=run_id,
            output=output,
            sources=all_sources,
            pdf_hashes=pdf_hashes,
        )
        self._log(run_id, "market_memo_written", "tool_call", "succeeded",
                  {"path": str(memo_path)})

        return output

    def _run_patent_finance_stage(
        self,
        run_id: str,
        enrichment: dict[str, Any],
        pdf_hashes: dict[str, str],
    ) -> Any:  # PatentFinanceAgentOutput
        """Run patent/finance analysis using scientific and market stage outputs."""
        from app.schemas.patent_finance import PatentFinanceAgentInput
        
        inn_data = enrichment.get("normalized_inn", {})
        disease_data = enrichment.get("normalized_disease") or {}

        # Load scientific and market outputs from DB for context
        scientific_json = self.db.get_scientific_output(run_id)
        sci_output: dict[str, Any] = {}
        if scientific_json:
            import contextlib
            with contextlib.suppress(json.JSONDecodeError):
                sci_output = json.loads(scientific_json)

        # Load market output
        market_stages = [s for s in self.db.get_stage_outputs(run_id) if s.stage == "market_analysis"]
        market_output: dict[str, Any] = {}
        if market_stages:
            with contextlib.suppress(json.JSONDecodeError):
                market_output = json.loads(market_stages[0].output_json)

        # Reuse evidence from scientific stage
        stored_sources = self.db.get_scientific_sources(run_id)
        stored_evidence = self.db.get_scientific_evidence_items(run_id)

        from app.schemas.evidence import EvidenceItem, SourceRecord
        all_sources = [SourceRecord.model_validate(s) for s in stored_sources]
        all_evidence = []
        for e_row in stored_evidence:
            if isinstance(e_row.get("key_findings"), str):
                e_row["key_findings"] = json.loads(e_row["key_findings"])
            all_evidence.append(EvidenceItem.model_validate(e_row))

        # MVP4: Collect patent evidence from patent connectors
        query = ConnectorQuery(
            inn=inn_data.get("preferred_name", ""),
            disease=disease_data.get("preferred_name"),
            synonyms=inn_data.get("synonyms", []),
            brand_names=inn_data.get("brand_names", []),
            max_results=20,
        )

        _log = logging.getLogger("pharm_agent.orchestrator")
        _log.info("[%s] Collecting patent evidence...", run_id)

        # ----------------------------------------------------------------
        # Step 1: Search Russian/Eurasian patent sources via PatentAggregator
        # ----------------------------------------------------------------
        _log.info("[%s] Searching Russian and Eurasian patent sources (Rospatent, FIPS, EAPO)...", run_id)
        
        ru_patent_query = PatentQuery(
            inn=inn_data.get("preferred_name", ""),
            inn_english=inn_data.get("english_inn"),
            inn_russian=inn_data.get("russian_name"),
            inn_synonyms=inn_data.get("synonyms", []),
            brand_names=inn_data.get("brand_names", []),
            molecular_target=inn_data.get("molecular_target"),
            indication=disease_data.get("preferred_name"),
            indication_synonyms=disease_data.get("synonyms", []),
            max_results=query.max_results,
        )
        
        try:
            patent_aggregator = PatentAggregator()
            ru_ea_result = patent_aggregator.search_all_sources(
                ru_patent_query,
                run_id=run_id,
                include_international=True,  # EPO, WIPO
                include_us=False,  # USPTO handled separately below
            )
            
            # Convert PatentEvidence to SourceRecord and EvidenceItem
            for patent in ru_ea_result.all_patents:
                source_record = SourceRecord(
                    source_id=patent.source_id,
                    source_type=patent.source_type,
                    title=patent.title,
                    publication_date=patent.publication_date,
                    url_or_path=patent.source_url,
                    external_id=patent.document_number,
                    retrieved_at=patent.retrieved_at,
                    metadata={
                        "jurisdiction": patent.jurisdiction,
                        "application_number": patent.application_number,
                        "filing_date": patent.filing_date,
                        "grant_date": patent.grant_date,
                        "legal_status": patent.legal_status.value if patent.legal_status else "unknown",
                        "applicants": patent.applicants,
                        "patent_holders": patent.patent_holders,
                        "ipc_codes": patent.ipc_codes,
                        "blocking_risk": patent.blocking_risk_preliminary.value if patent.blocking_risk_preliminary else "unknown",
                    },
                )
                all_sources.append(source_record)
                
                evidence_item = EvidenceItem(
                    source_id=patent.source_id,
                    relevance_score=0.7 if patent.blocking_risk_preliminary.value in ["high", "medium"] else 0.5,
                    summary=f"{patent.title} ({patent.jurisdiction} {patent.document_number})",
                    key_findings=[
                        f"Legal status: {patent.legal_status.value}",
                        f"Blocking risk: {patent.blocking_risk_preliminary.value}",
                    ] + (patent.warnings[:3] if patent.warnings else []),
                )
                all_evidence.append(evidence_item)
            
            # Log RU/EA patent search results per source
            _log.info(
                "[%s] RU/EA patent search completed: %d patents found total",
                run_id, len(ru_ea_result.all_patents),
            )
            
            # Log individual source results
            if ru_ea_result.rospatent_results:
                _log.info(
                    "[%s]   Rospatent: %d patents (available: %s)",
                    run_id,
                    ru_ea_result.rospatent_results.results_returned,
                    ru_ea_result.rospatent_results.source_available,
                )
            if ru_ea_result.fips_results:
                _log.info(
                    "[%s]   FIPS: %d patents (available: %s)",
                    run_id,
                    ru_ea_result.fips_results.results_returned,
                    ru_ea_result.fips_results.source_available,
                )
            if ru_ea_result.eapo_results:
                _log.info(
                    "[%s]   EAPO: %d patents (available: %s)",
                    run_id,
                    ru_ea_result.eapo_results.results_returned,
                    ru_ea_result.eapo_results.source_available,
                )
            if ru_ea_result.epo_results:
                _log.info(
                    "[%s]   EPO OPS: %d patents",
                    run_id,
                    ru_ea_result.epo_results.results_returned,
                )
            
            _log.info(
                "[%s] Sources queried: %s | Available: %s | Unavailable: %s",
                run_id,
                ru_ea_result.sources_queried,
                ru_ea_result.sources_available,
                ru_ea_result.sources_unavailable,
            )
            if ru_ea_result.requires_manual_review:
                _log.warning(
                    "[%s] RU/EA patent search requires manual review: %s",
                    run_id, ru_ea_result.manual_review_reasons,
                )
            if ru_ea_result.total_warnings:
                _log.info("[%s] RU/EA patent warnings: %s", run_id, ru_ea_result.total_warnings[:5])
                
        except Exception as exc:
            _log.warning("[%s] PatentAggregator (RU/EA) failed: %s", run_id, exc)
            # Continue with international sources

        # ----------------------------------------------------------------
        # Step 2: Search US-specific patent sources (Orange Book, Purple Book, USPTO)
        # ----------------------------------------------------------------
        _log.info("[%s] Searching US patent sources (Orange Book, Purple Book, USPTO)...", run_id)
        
        patent_connector_classes = [
            OrangeBookConnector,
            PurpleBookConnector,
            USPTOConnector,
        ]
        patent_results: list[ConnectorResult] = []
        for ConnectorClass in patent_connector_classes:
            try:
                connector = ConnectorClass()
                result = connector.search(query, run_id=run_id)
                patent_results.append(result)
            except Exception as exc:
                _log.warning(
                    "[%s] Patent connector %s crashed: %s", run_id, ConnectorClass.connector_name, exc,
                )
                patent_results.append(ConnectorResult(
                    connector_name=ConnectorClass.connector_name,
                    query=query,
                    errors=[f"{type(exc).__name__}: {exc}"],
                ))

        # Merge US patent evidence with all evidence
        patent_sources, patent_evidence = merge_connector_results(patent_results)
        all_sources.extend(patent_sources)
        all_evidence.extend(patent_evidence)

        # Log US patent findings
        for cr in patent_results:
            if cr.warnings:
                _log.info("[%s] %s warnings: %s", run_id, cr.connector_name, cr.warnings)
            _log.info(
                "[%s] %s returned %d patent sources",
                run_id, cr.connector_name, cr.results_returned,
            )

        # Build patent/finance agent input
        moa_text = sci_output.get("mechanism_of_action", {})
        if isinstance(moa_text, dict):
            moa_text = moa_text.get("claim", "")

        # Build PDF context for patent/finance analysis
        # PDFs may contain patent documents, financial reports, due diligence materials
        pdf_context = self._build_pdf_context_for_patent_finance(run_id)

        agent_input = PatentFinanceAgentInput(
            run_id=run_id,
            inn_preferred=inn_data.get("preferred_name", ""),
            inn_english=inn_data.get("english_inn"),
            inn_synonyms=inn_data.get("synonyms", []),
            disease_preferred=disease_data.get("preferred_name"),
            disease_synonyms=disease_data.get("synonyms", []),
            region=enrichment.get("region"),
            molecule_type=inn_data.get("molecule_type", "unknown"),
            stage=enrichment.get("stage"),
            scientific_summary=sci_output.get("executive_summary", ""),
            mechanism_of_action=moa_text,
            approved_therapies_json=json.dumps(
                sci_output.get("approved_therapies", []), ensure_ascii=False
            ) if sci_output.get("approved_therapies") else None,
            market_summary=market_output.get("market_summary", ""),
            competitors_json=json.dumps(
                market_output.get("competitors", []), ensure_ascii=False
            ) if market_output.get("competitors") else None,
            market_size_estimate=market_output.get("market_size_estimate"),
            evidence_items_json=json.dumps(
                [e.model_dump(mode="json") for e in all_evidence], ensure_ascii=False
            ),
            sources_json=json.dumps(
                [s.model_dump(mode="json") for s in all_sources], ensure_ascii=False
            ),
            pdf_hashes=pdf_hashes,
            pdf_context=pdf_context,
        )

        # Call patent/finance agent
        agent = PatentFinanceAgent(client=self._get_llm_client())
        output = agent.run(agent_input, all_sources, all_evidence)

        # Persist patent/finance output
        output_json = json.dumps(output.model_dump(mode="json"), ensure_ascii=False)
        self.db.save_stage_output(StageOutput(
            run_id=run_id,
            stage="patent_finance_analysis",
            output_json=output_json,
            status="succeeded",
            created_at=_now_iso(),
        ))

        self.db.update_run_status(run_id, RunStatus.patent_finance_analyzed)
        self._log(run_id, "patent_finance_analyzed", "state_change", "succeeded")

        # Write patent/finance memo to Obsidian
        memo_path = obsidian.write_patent_finance_memo(
            run_id=run_id,
            output=output,
            sources=all_sources,
            pdf_hashes=pdf_hashes,
        )
        self._log(run_id, "patent_finance_memo_written", "tool_call", "succeeded",
                  {"path": str(memo_path)})

        return output

    # Keep backward-compatible aliases used by existing tests
    def submit_human_decision(self, run_id: str, decision: HumanDecision) -> RunRecord:
        run, _ = self.finalize_decision(run_id, decision)
        return run

    # ------------------------------------------------------------------
    # Revision re-run
    # ------------------------------------------------------------------

    def rerun_from_revision(
        self,
        run_id: str,
        corrected_input: RawInput,
        pdf_paths: list[Path],
    ) -> tuple[RunRecord, HumanVerificationPacket | None]:
        """Re-run enrichment after a needs_revision decision.

        Applies corrected input, re-ingests PDFs, re-runs enrichment,
        and returns the run back to awaiting_human_verification.
        """
        run = self.db.get_run(run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found")
        if run.status != RunStatus.needs_revision:
            raise ValueError(f"Run not in needs_revision state (status={run.status.value})")

        # Transition back to input_collected
        run = self.db.update_run_status(run_id, RunStatus.input_collected)
        self._log(run_id, "revision_restart", "state_change", "succeeded",
                  {"corrected_input": corrected_input.model_dump(mode="json")})

        # Re-register PDFs
        pdf_results = self._register_and_ingest_pdfs(run_id, pdf_paths)
        run = self.db.update_run_status(run_id, RunStatus.pdfs_registered)
        run = self.db.update_run_status(run_id, RunStatus.pdfs_ingested)

        # Re-run enrichment (PDF not used - analyzed in later stages)
        try:
            agent = IntakeEnrichmentAgent(client=self._get_llm_client())
            enrichment = agent.run(corrected_input, run_id=run_id)
            self.db.save_enrichment_output(run_id, enrichment)
        except StructuredOutputError as exc:
            run = self.db.update_run_status(run_id, RunStatus.failed, error=str(exc))
            self._log(run_id, "intake_enrichment", "error", "failed",
                      {"error": str(exc), "type": "structured_output"})
            obsidian.write_run_note(run)
            return run, None

        run = self.db.update_run_status(run_id, RunStatus.intake_enriched)
        run = self.db.update_run_status(run_id, RunStatus.awaiting_human_verification)
        self._log(run_id, "revision_enriched", "state_change", "succeeded")

        obsidian.write_run_note(run)
        packet = self.build_verification_packet(run_id)
        return run, packet

    # ------------------------------------------------------------------
    # PDF context building for patent/finance analysis
    # ------------------------------------------------------------------

    def _build_pdf_context_for_patent_finance(self, run_id: str, max_chars: int = 20000) -> str | None:
        """Build PDF context for patent/finance analysis.

        Extracts text from stored PDF extraction results and formats it
        for use in the patent/finance agent prompt. PDFs may contain
        patent documents, financial reports, due diligence materials,
        market research, or technical documentation.

        Args:
            run_id: The run ID to load PDF extractions for.
            max_chars: Maximum total characters to include (to avoid prompt overflow).

        Returns:
            Formatted PDF context string or None if no PDFs available.
        """
        stage_outputs = self.db.get_stage_outputs(run_id)
        pdf_extractions = [
            s for s in stage_outputs if s.stage == "pdf_extraction"
        ]

        if not pdf_extractions:
            return None

        lines: list[str] = []
        total_chars = 0

        for stage_out in pdf_extractions:
            extraction = PDFExtractionResult.model_validate_json(stage_out.output_json)
            lines.append(f"\n=== PDF: {extraction.pdf_id} ({extraction.page_count} pages, sha256={extraction.sha256[:12]}...) ===\n")

            for chunk in extraction.chunks:
                # Limit per-page text to avoid overwhelming the context
                page_text = chunk.text[:2000] if len(chunk.text) > 2000 else chunk.text
                page_header = f"[Page {chunk.page_number}]\n"
                page_content = page_header + page_text.strip()

                if total_chars + len(page_content) > max_chars:
                    lines.append(f"\n... (truncated at {max_chars} chars total) ...")
                    break

                lines.append(page_content)
                total_chars += len(page_content)

            if total_chars >= max_chars:
                break

        if not lines:
            return None

        return "\n".join(lines)

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
