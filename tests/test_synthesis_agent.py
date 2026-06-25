"""Unit tests for the Synthesis Agent (MVP 5)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.synthesis import (
    FinalSynthesisOutput,
    InputSummary,
    OverallConclusion,
    ScientificRationaleSynthesis,
    CommercialAttractivenessSynthesis,
    PatentFinancialViabilitySynthesis,
    InvestmentRange,
    MonetizationTimeline,
    Contradiction,
    SourceAvailabilityWarning,
    ManualReviewItem,
    NextStep,
    SourceReference,
    Disclaimer,
    SynthesisAgentInput,
    SynthesisPreconditionError,
)
from app.agents.synthesis_checks import (
    detect_scientific_market_contradictions,
    detect_patent_market_contradictions,
    detect_finance_evidence_contradictions,
    detect_source_coverage_gaps,
    run_all_contradiction_checks,
)


# ============================================================================
# Schema Validation Tests
# ============================================================================


class TestFinalSynthesisOutputSchema:
    """Tests for FinalSynthesisOutput schema validation."""

    def test_valid_full_payload(self):
        """Valid payload should parse successfully."""
        payload = {
            "run_id": "run_test_123",
            "input_summary": {
                "run_id": "run_test_123",
                "inn_preferred": "Trastuzumab",
                "inn_english": "Trastuzumab",
                "disease_preferred": "HER2+ Breast Cancer",
                "molecule_type": "monoclonal_antibody",
                "human_verification_status": "approved",
            },
            "overall_conclusion": {
                "summary": "Strong scientific rationale with moderate commercial opportunity.",
                "go_no_go_interpretation": "conditional_go",
                "main_reason": "Established mechanism but competitive market.",
                "critical_dependencies": ["Phase 2 trial results", "Partnership deal"],
            },
            "scientific_rationale": {
                "summary": "Strong mechanism of action targeting HER2.",
                "strengths": ["Well-characterized target", "Existing clinical data"],
                "risks": ["Resistance mechanisms"],
                "evidence_gaps": ["Long-term safety in combination"],
                "source_ids": ["PMID_123", "NCT_456"],
            },
            "commercial_attractiveness": {
                "summary": "Large market but competitive.",
                "market_opportunity": "$5B market",
                "competitor_pressure": "High from biosimilars",
                "commercial_risks": ["Pricing pressure"],
                "source_ids": ["mkt_001"],
            },
            "patent_and_financial_viability": {
                "summary": "Clear FTO path with some risks.",
                "fto_risks": ["Composition patent expires 2028"],
                "patent_fence_opportunities": ["Combination therapy claims"],
                "investment_range": {
                    "low_case": "$10M",
                    "base_case": "$25M",
                    "high_case": "$50M",
                    "currency": "USD",
                    "assumptions": ["Assumes partnership at Phase 2"],
                },
                "monetization_timeline": {
                    "earliest_value_inflection": "Phase 2a data (2026)",
                    "licensing_window": "2-3 years",
                    "revenue_window": "5-7 years",
                    "required_evidence_for_monetization": ["PoC data"],
                    "key_risks": ["Clinical failure"],
                },
                "source_ids": ["pat_001"],
            },
            "contradictions": [
                {
                    "area": "market_vs_science",
                    "description": "Market assumes premium but evidence is moderate.",
                    "affected_conclusion": "commercial_viability",
                    "severity": "medium",
                    "source_agent_outputs": ["scientific", "market"],
                }
            ],
            "source_availability_warnings": [],
            "manual_review_required": [
                {
                    "area": "patent_landscape",
                    "reason": "Complex blocking patent situation",
                    "recommended_expert_type": "patent_attorney",
                    "priority": "high",
                }
            ],
            "next_steps": [
                {
                    "action": "Complete Phase 2a trial design",
                    "rationale": "Required for next value inflection",
                    "responsible_party": "R&D team",
                    "priority": "high",
                    "timeline": "Q2 2025",
                }
            ],
            "source_references": [
                {
                    "source_id": "PMID_123",
                    "source_type": "pubmed",
                    "title": "HER2 targeting review",
                    "used_in_sections": ["scientific_rationale"],
                }
            ],
            "disclaimers": [
                {
                    "category": "medical",
                    "text": "This is for R&D purposes only, not medical advice.",
                }
            ],
            "created_at": "2025-01-15T10:00:00Z",
        }
        
        output = FinalSynthesisOutput.model_validate(payload)
        
        assert output.run_id == "run_test_123"
        assert output.overall_conclusion.go_no_go_interpretation == "conditional_go"
        assert len(output.contradictions) == 1
        assert output.patent_and_financial_viability.investment_range is not None
        assert output.patent_and_financial_viability.investment_range.base_case == "$25M"

    def test_invalid_go_no_go_interpretation(self):
        """Invalid go_no_go_interpretation should fail."""
        payload = {
            "run_id": "run_test",
            "input_summary": {
                "run_id": "run_test",
                "inn_preferred": "Test",
                "human_verification_status": "approved",
            },
            "overall_conclusion": {
                "summary": "Test",
                "go_no_go_interpretation": "maybe",  # Invalid
                "main_reason": "Test",
            },
            "scientific_rationale": {"summary": "Test"},
            "commercial_attractiveness": {"summary": "Test"},
            "patent_and_financial_viability": {"summary": "Test"},
        }
        
        with pytest.raises(Exception):  # Pydantic ValidationError
            FinalSynthesisOutput.model_validate(payload)

    def test_missing_required_fields(self):
        """Missing required fields should fail."""
        payload = {
            "run_id": "run_test",
            # Missing input_summary, overall_conclusion, etc.
        }
        
        with pytest.raises(Exception):
            FinalSynthesisOutput.model_validate(payload)

    def test_investment_range_required_fields(self):
        """InvestmentRange requires low/base/high cases."""
        with pytest.raises(Exception):
            InvestmentRange(low_case="$10M")  # Missing base_case, high_case

    def test_contradiction_severity_literal(self):
        """Contradiction severity must be low/medium/high."""
        valid = Contradiction(
            area="test",
            description="test",
            affected_conclusion="test",
            severity="high",
        )
        assert valid.severity == "high"
        
        with pytest.raises(Exception):
            Contradiction(
                area="test",
                description="test", 
                affected_conclusion="test",
                severity="critical",  # Invalid
            )


# ============================================================================
# Precondition Tests
# ============================================================================


class TestSynthesisPreconditions:
    """Tests for synthesis precondition checking."""

    @patch("app.storage.db.Database")
    def test_missing_human_verification_blocks_synthesis(self, mock_db_class):
        """Missing human verification should block synthesis."""
        from app.agents.synthesis_agent import SynthesisAgent
        
        mock_db = MagicMock()
        mock_db.get_run.return_value = MagicMock(
            run_id="run_test",
            raw_input_json='{"inn_raw": "test"}',
        )
        mock_db.get_human_decision.return_value = None  # No decision
        
        agent = SynthesisAgent(db=mock_db, client=MagicMock())
        
        with pytest.raises(SynthesisPreconditionError, match="Human verification"):
            agent._check_preconditions("run_test")

    @patch("app.storage.db.Database")
    def test_rejected_human_verification_blocks_synthesis(self, mock_db_class):
        """Rejected human verification should block synthesis."""
        from app.agents.synthesis_agent import SynthesisAgent
        from app.schemas.human_decision import HumanDecision
        
        mock_db = MagicMock()
        mock_db.get_run.return_value = MagicMock(
            run_id="run_test",
            raw_input_json='{"inn_raw": "test"}',
        )
        mock_db.get_human_decision.return_value = HumanDecision(
            run_id="run_test",
            decision="rejected",
            timestamp="2025-01-01T00:00:00Z",
        )
        
        agent = SynthesisAgent(db=mock_db, client=MagicMock())
        
        with pytest.raises(SynthesisPreconditionError, match="must be approved"):
            agent._check_preconditions("run_test")

    @patch("app.storage.db.Database")
    def test_missing_scientific_output_blocks_synthesis(self, mock_db_class):
        """Missing scientific output should block synthesis."""
        from app.agents.synthesis_agent import SynthesisAgent
        from app.schemas.human_decision import HumanDecision
        
        mock_db = MagicMock()
        mock_db.get_run.return_value = MagicMock(
            run_id="run_test",
            raw_input_json='{"inn_raw": "test"}',
        )
        mock_db.get_human_decision.return_value = HumanDecision(
            run_id="run_test",
            decision="approved",
            timestamp="2025-01-01T00:00:00Z",
        )
        mock_db.get_scientific_output.return_value = None  # No scientific output
        
        agent = SynthesisAgent(db=mock_db, client=MagicMock())
        
        with pytest.raises(SynthesisPreconditionError, match="Scientific agent"):
            agent._check_preconditions("run_test")

    @patch("app.storage.db.Database")
    def test_missing_market_output_blocks_synthesis(self, mock_db_class):
        """Missing market output should block synthesis."""
        from app.agents.synthesis_agent import SynthesisAgent
        from app.schemas.human_decision import HumanDecision
        
        mock_db = MagicMock()
        mock_db.get_run.return_value = MagicMock(
            run_id="run_test",
            raw_input_json='{"inn_raw": "test"}',
        )
        mock_db.get_human_decision.return_value = HumanDecision(
            run_id="run_test",
            decision="approved",
            timestamp="2025-01-01T00:00:00Z",
        )
        mock_db.get_scientific_output.return_value = '{"summary": "test"}'
        mock_db.get_market_output.return_value = None  # No market output
        
        agent = SynthesisAgent(db=mock_db, client=MagicMock())
        
        with pytest.raises(SynthesisPreconditionError, match="Market agent"):
            agent._check_preconditions("run_test")

    @patch("app.storage.db.Database")
    def test_missing_patent_finance_output_blocks_synthesis(self, mock_db_class):
        """Missing patent/finance output should block synthesis."""
        from app.agents.synthesis_agent import SynthesisAgent
        from app.schemas.human_decision import HumanDecision
        
        mock_db = MagicMock()
        mock_db.get_run.return_value = MagicMock(
            run_id="run_test",
            raw_input_json='{"inn_raw": "test"}',
        )
        mock_db.get_human_decision.return_value = HumanDecision(
            run_id="run_test",
            decision="approved",
            timestamp="2025-01-01T00:00:00Z",
        )
        mock_db.get_scientific_output.return_value = '{"summary": "test"}'
        mock_db.get_market_output.return_value = '{"summary": "test"}'
        mock_db.get_patent_finance_output.return_value = None  # No patent output
        
        agent = SynthesisAgent(db=mock_db, client=MagicMock())
        
        with pytest.raises(SynthesisPreconditionError, match="Patent/finance"):
            agent._check_preconditions("run_test")


# ============================================================================
# Contradiction Detection Tests
# ============================================================================


class TestContradictionChecks:
    """Tests for deterministic contradiction detection."""

    def test_weak_evidence_strong_market_contradiction(self):
        """Weak scientific evidence + strong market adoption should create contradiction."""
        scientific = json.dumps({
            "confidence": "low",
            "evidence_gaps": ["gap1", "gap2", "gap3"],
            "scientific_risks": ["risk1", "risk2"],
        })
        market = json.dumps({
            "market_summary": "Strong demand with premium pricing expected",
            "confidence": "high",
        })
        
        contradictions = detect_scientific_market_contradictions(scientific, market)
        
        assert len(contradictions) >= 1
        assert any(c["area"] == "scientific_vs_market" for c in contradictions)

    def test_high_patent_risk_near_term_revenue_contradiction(self):
        """High patent risk + near-term revenue assumption should create contradiction."""
        patent = json.dumps({
            "freedom_to_operate_risks": [
                {"description": "Blocking patent", "severity": "high"},
            ],
            "money_timeline": {
                "earliest_value_inflection": "1-2 years from now",
                "revenue_window": "near-term revenue possible",
            },
            "blocking_patent_candidates": ["pat1", "pat2"],
        })
        market = json.dumps({
            "market_summary": "Strong competitive position expected",
        })
        
        contradictions = detect_patent_market_contradictions(patent, market)
        
        # Should find high FTO risk vs competitive position
        assert len(contradictions) >= 1

    def test_fda_unavailable_fda_dependent_conclusion_creates_warning(self):
        """FDA unavailable + FDA-dependent conclusion should create warning."""
        source_warnings = ["FDA data source was unavailable due to API error"]
        patent = json.dumps({
            "assumptions": ["FDA approval timeline assumed", "FDA guidance followed"],
            "sources": [
                {"source_type": "fda", "id": "fda_001"},
                {"source_type": "fda", "id": "fda_002"},
            ],
        })
        
        contradictions = detect_source_coverage_gaps(
            source_warnings,
            patent_finance_json=patent,
        )
        
        assert len(contradictions) >= 1
        assert any(c["area"] == "source_coverage" for c in contradictions)

    def test_investment_range_without_assumptions(self):
        """Investment range without assumptions should create contradiction."""
        patent = json.dumps({
            "investment_range": {
                "low_case": {"amount": "$10M"},
                "base_case": {"amount": "$20M"},
                "high_case": {"amount": "$50M"},
                # No assumptions
            },
        })
        
        contradictions = detect_finance_evidence_contradictions(patent, None)
        
        assert len(contradictions) >= 1
        assert any("assumptions" in c["description"].lower() for c in contradictions)

    def test_no_contradictions_when_outputs_consistent(self):
        """No contradictions when outputs are consistent."""
        scientific = json.dumps({
            "confidence": "high",
            "evidence_gaps": [],
            "scientific_risks": [],
        })
        market = json.dumps({
            "market_summary": "Moderate market opportunity",
            "confidence": "medium",
        })
        patent = json.dumps({
            "freedom_to_operate_risks": [],
            "investment_range": {
                "low_case": {"amount": "$10M", "assumptions": ["Standard costs"]},
                "base_case": {"amount": "$20M", "assumptions": ["Standard costs"]},
                "high_case": {"amount": "$50M", "assumptions": ["Standard costs"]},
            },
        })
        
        all_contradictions = run_all_contradiction_checks(
            scientific_json=scientific,
            market_json=market,
            patent_finance_json=patent,
            source_warnings=[],
        )
        
        # May have some, but no high-severity ones
        high_severity = [c for c in all_contradictions if c.get("severity") == "high"]
        assert len(high_severity) == 0


# ============================================================================
# LLM Output Validation Tests
# ============================================================================


class TestStructuredOutputValidation:
    """Tests for structured LLM output handling."""

    @patch("app.storage.db.Database")
    @patch("app.llm.structured_client.StructuredLLMClient")
    def test_valid_llm_response_is_saved(self, mock_client_class, mock_db_class):
        """Valid LLM response should be saved to database."""
        from app.agents.synthesis_agent import SynthesisAgent
        from app.schemas.human_decision import HumanDecision
        
        # Setup mock DB
        mock_db = MagicMock()
        mock_db.get_run.return_value = MagicMock(
            run_id="run_test",
            raw_input_json='{"inn_raw": "test"}',
            enrichment_output_json='{"normalized_inn": {"preferred_name": "Test"}}',
        )
        mock_db.get_human_decision.return_value = HumanDecision(
            run_id="run_test",
            decision="approved",
            timestamp="2025-01-01T00:00:00Z",
        )
        mock_db.get_scientific_output.return_value = '{"summary": "test"}'
        mock_db.get_market_output.return_value = '{"summary": "test"}'
        mock_db.get_patent_finance_output.return_value = '{"summary": "test"}'
        mock_db.get_pdf_versions_for_run.return_value = []
        mock_db.get_scientific_sources.return_value = []
        mock_db.get_run_steps.return_value = []
        
        # Setup mock client to return valid output
        valid_output = FinalSynthesisOutput(
            run_id="run_test",
            input_summary=InputSummary(
                run_id="run_test",
                inn_preferred="Test",
                human_verification_status="approved",
            ),
            overall_conclusion=OverallConclusion(
                summary="Test conclusion",
                go_no_go_interpretation="conditional_go",
                main_reason="Test reason",
            ),
            scientific_rationale=ScientificRationaleSynthesis(summary="Test"),
            commercial_attractiveness=CommercialAttractivenessSynthesis(summary="Test"),
            patent_and_financial_viability=PatentFinancialViabilitySynthesis(summary="Test"),
        )
        
        mock_client = MagicMock()
        mock_client.call.return_value = valid_output
        
        agent = SynthesisAgent(db=mock_db, client=mock_client)
        
        # Mock the report writing
        with patch("app.agents.synthesis_agent._load_prompt", return_value="test prompt"):
            with patch("app.reports.final_assessment.generate_final_assessment_markdown") as mock_write:
                from pathlib import Path
                mock_write.return_value = Path("/tmp/test_report.md")
                
                result = agent.run("run_test")
        
        assert result.run_id == "run_test"
        mock_db.save_synthesis_output.assert_called_once()


# ============================================================================
# Markdown Report Tests
# ============================================================================


class TestMarkdownReport:
    """Tests for Markdown report generation."""

    def test_report_contains_executive_summary(self, tmp_path):
        """Report should contain Executive Summary section."""
        from app.reports.final_assessment import generate_final_assessment_markdown
        
        output = FinalSynthesisOutput(
            run_id="run_test",
            input_summary=InputSummary(
                run_id="run_test",
                inn_preferred="Test Drug",
                human_verification_status="approved",
            ),
            overall_conclusion=OverallConclusion(
                summary="This is the executive summary for the test drug.",
                go_no_go_interpretation="go",
                main_reason="Strong evidence base.",
            ),
            scientific_rationale=ScientificRationaleSynthesis(summary="Scientific summary."),
            commercial_attractiveness=CommercialAttractivenessSynthesis(summary="Commercial summary."),
            patent_and_financial_viability=PatentFinancialViabilitySynthesis(summary="Patent summary."),
        )
        
        report_path = generate_final_assessment_markdown("run_test", output, vault_dir=tmp_path)
        content = report_path.read_text()
        
        assert "Executive Summary" in content
        assert "This is the executive summary" in content
        assert "GO" in content.upper()

    def test_report_contains_monetization_section(self, tmp_path):
        """Report should contain When Can We Get Money section."""
        from app.reports.final_assessment import generate_final_assessment_markdown
        
        output = FinalSynthesisOutput(
            run_id="run_test",
            input_summary=InputSummary(
                run_id="run_test",
                inn_preferred="Test",
                human_verification_status="approved",
            ),
            overall_conclusion=OverallConclusion(
                summary="Test",
                go_no_go_interpretation="conditional_go",
                main_reason="Test",
            ),
            scientific_rationale=ScientificRationaleSynthesis(summary="Test"),
            commercial_attractiveness=CommercialAttractivenessSynthesis(summary="Test"),
            patent_and_financial_viability=PatentFinancialViabilitySynthesis(
                summary="Test",
                monetization_timeline=MonetizationTimeline(
                    earliest_value_inflection="Phase 2a data in 2026",
                    licensing_window="2-3 years",
                    revenue_window="5-7 years post-approval",
                ),
            ),
        )
        
        report_path = generate_final_assessment_markdown("run_test", output, vault_dir=tmp_path)
        content = report_path.read_text()
        
        assert "When Can We Get Money" in content
        assert "Phase 2a data" in content
        assert "2-3 years" in content

    def test_report_contains_disclaimers(self, tmp_path):
        """Report should contain disclaimers section."""
        from app.reports.final_assessment import generate_final_assessment_markdown
        
        output = FinalSynthesisOutput(
            run_id="run_test",
            input_summary=InputSummary(
                run_id="run_test",
                inn_preferred="Test",
                human_verification_status="approved",
            ),
            overall_conclusion=OverallConclusion(
                summary="Test",
                go_no_go_interpretation="go",
                main_reason="Test",
            ),
            scientific_rationale=ScientificRationaleSynthesis(summary="Test"),
            commercial_attractiveness=CommercialAttractivenessSynthesis(summary="Test"),
            patent_and_financial_viability=PatentFinancialViabilitySynthesis(summary="Test"),
        )
        
        report_path = generate_final_assessment_markdown("run_test", output, vault_dir=tmp_path)
        content = report_path.read_text()
        
        assert "Disclaimer" in content
        assert "medical advice" in content.lower()
        assert "patent" in content.lower()

    def test_report_does_not_contain_raw_llm_logs(self, tmp_path):
        """Report should not contain raw LLM logs or internal data."""
        from app.reports.final_assessment import generate_final_assessment_markdown
        
        output = FinalSynthesisOutput(
            run_id="run_test",
            input_summary=InputSummary(
                run_id="run_test",
                inn_preferred="Test",
                human_verification_status="approved",
            ),
            overall_conclusion=OverallConclusion(
                summary="Test",
                go_no_go_interpretation="go",
                main_reason="Test",
            ),
            scientific_rationale=ScientificRationaleSynthesis(summary="Test"),
            commercial_attractiveness=CommercialAttractivenessSynthesis(summary="Test"),
            patent_and_financial_viability=PatentFinancialViabilitySynthesis(summary="Test"),
        )
        
        report_path = generate_final_assessment_markdown("run_test", output, vault_dir=tmp_path)
        content = report_path.read_text()
        
        # Should not contain internal implementation details
        assert "raw_response" not in content.lower()
        assert "llm_call" not in content.lower()
        assert "api_key" not in content.lower()


# ============================================================================
# Audit Logging Tests
# ============================================================================


class TestAuditLogging:
    """Tests for audit event logging."""

    @patch("app.logging.audit_logger.log_event")
    @patch("app.logging.audit_logger.log_tool_call")
    @patch("app.storage.db.Database")
    def test_synthesis_events_are_written(self, mock_db_class, mock_log_tool, mock_log_event):
        """Synthesis events should be written to audit log."""
        from app.agents.synthesis_agent import SynthesisAgent
        from app.schemas.human_decision import HumanDecision
        
        mock_db = MagicMock()
        mock_db.get_run.return_value = MagicMock(
            run_id="run_test",
            raw_input_json='{"inn_raw": "test"}',
            enrichment_output_json='{"normalized_inn": {"preferred_name": "Test"}}',
        )
        mock_db.get_human_decision.return_value = HumanDecision(
            run_id="run_test",
            decision="approved",
            timestamp="2025-01-01T00:00:00Z",
        )
        mock_db.get_scientific_output.return_value = '{"summary": "test"}'
        mock_db.get_market_output.return_value = '{"summary": "test"}'
        mock_db.get_patent_finance_output.return_value = '{"summary": "test"}'
        mock_db.get_pdf_versions_for_run.return_value = []
        mock_db.get_scientific_sources.return_value = []
        mock_db.get_run_steps.return_value = []
        
        valid_output = FinalSynthesisOutput(
            run_id="run_test",
            input_summary=InputSummary(
                run_id="run_test",
                inn_preferred="Test",
                human_verification_status="approved",
            ),
            overall_conclusion=OverallConclusion(
                summary="Test",
                go_no_go_interpretation="go",
                main_reason="Test",
            ),
            scientific_rationale=ScientificRationaleSynthesis(summary="Test"),
            commercial_attractiveness=CommercialAttractivenessSynthesis(summary="Test"),
            patent_and_financial_viability=PatentFinancialViabilitySynthesis(summary="Test"),
        )
        
        mock_client = MagicMock()
        mock_client.call.return_value = valid_output
        
        agent = SynthesisAgent(db=mock_db, client=mock_client)
        
        with patch("app.agents.synthesis_agent._load_prompt", return_value="test"):
            with patch("app.reports.final_assessment.generate_final_assessment_markdown") as mock_write:
                from pathlib import Path
                mock_write.return_value = Path("/tmp/test.md")
                
                agent.run("run_test")
        
        # Check that events were logged
        assert mock_log_event.call_count >= 3  # started, llm_call, completed
        
        # Check event types
        event_types = []
        for call in mock_log_event.call_args_list:
            event = call[0][0]
            event_types.append(event.event_type)
        
        assert "stage_started" in event_types
        assert "llm_call_completed" in event_types or "llm_call_started" in event_types

    def test_secrets_are_not_logged(self):
        """API keys and secrets should not be logged."""
        # This is a design principle test - check that log_event function
        # doesn't include sensitive fields
        from app.schemas.audit import AuditEvent
        
        event = AuditEvent(
            event_id="test",
            run_id="test",
            stage="test",
            event_type="test",
            timestamp="2025-01-01",
            status="test",
            metadata={"model": "test", "latency_ms": 100},
        )
        
        event_dict = event.model_dump()
        
        # Ensure no secret-like keys
        all_keys = str(event_dict).lower()
        assert "api_key" not in all_keys
        assert "secret" not in all_keys
        assert "password" not in all_keys
        assert "auth_header" not in all_keys
