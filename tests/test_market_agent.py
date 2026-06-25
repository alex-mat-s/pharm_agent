"""Tests for the market agent (MVP 3)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.agents.market_agent import MarketAgent, _format_evidence_for_market, _format_scientific_context
from app.schemas.evidence import EvidenceCategory, EvidenceItem, SourceRecord
from app.schemas.market import (
    CommercialRisk,
    CompetitorEntry,
    MarketAgentInput,
    MarketAgentOutput,
    MarketDynamic,
    PatientPopulation,
)


def _make_input(**overrides) -> MarketAgentInput:
    defaults = {
        "run_id": "test-run",
        "inn_preferred": "metformin",
        "inn_english": "metformin",
        "disease_preferred": "type 2 diabetes",
    }
    defaults.update(overrides)
    return MarketAgentInput(**defaults)


def _make_output(**overrides) -> MarketAgentOutput:
    defaults = {
        "market_summary": "Test market summary",
        "patient_population": PatientPopulation(
            global_estimate="~400M",
            target_segment="T2DM inadequately controlled",
        ),
        "competitors": [
            CompetitorEntry(
                drug_name="sitagliptin",
                source_ids=["src-1"],
            ),
        ],
        "commercial_risks": [
            CommercialRisk(risk="Generic competition", severity="medium", source_ids=["src-1"]),
        ],
    }
    defaults.update(overrides)
    return MarketAgentOutput(**defaults)


def _make_source(source_id: str = "src-1") -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        source_type="pubmed",
        title="Test source",
        retrieved_at="2026-01-01T00:00:00Z",
        query_used="test query",
    )


def _make_evidence(source_id: str = "src-1") -> EvidenceItem:
    return EvidenceItem(
        evidence_id="evi-1",
        source_id=source_id,
        category=EvidenceCategory.other,
        summary="Test evidence",
        key_findings=["finding 1"],
    )


class TestMarketAgent:
    def test_run_calls_llm_and_returns_output(self):
        mock_client = MagicMock()
        expected = _make_output()
        mock_client.call.return_value = expected

        agent = MarketAgent(client=mock_client)
        inp = _make_input()
        sources = [_make_source()]
        evidence = [_make_evidence()]

        result = agent.run(inp, sources, evidence)

        assert isinstance(result, MarketAgentOutput)
        assert result.market_summary == "Test market summary"
        mock_client.call.assert_called_once()

    def test_validate_output_flags_orphan_ids(self):
        output = _make_output(
            competitors=[CompetitorEntry(drug_name="x", source_ids=["unknown-1"])]
        )
        MarketAgent._validate_output(output, {"src-1"})
        assert any("unknown-1" in m for m in output.missing_information)

    def test_validate_output_flags_no_competitors(self):
        output = _make_output(competitors=[])
        MarketAgent._validate_output(output, set())
        assert any("No competitors" in m for m in output.missing_information)

    def test_validate_output_flags_no_risks(self):
        output = _make_output(commercial_risks=[])
        MarketAgent._validate_output(output, set())
        assert any("No commercial risks" in m for m in output.missing_information)

    def test_validate_output_known_ids_not_flagged(self):
        output = _make_output()
        MarketAgent._validate_output(output, {"src-1"})
        orphan_msgs = [m for m in output.missing_information if "unknown source_ids" in m]
        assert len(orphan_msgs) == 0


class TestFormatters:
    def test_format_evidence_empty(self):
        assert "No evidence" in _format_evidence_for_market([])

    def test_format_evidence_basic(self):
        items = [_make_evidence()]
        result = _format_evidence_for_market(items)
        assert "src-1" in result
        assert "Test evidence" in result

    def test_format_scientific_context_empty(self):
        inp = _make_input()
        ctx = _format_scientific_context(inp)
        assert ctx["scientific_summary"] == "(not available)"
        assert ctx["approved_therapies"] == "(not available)"

    def test_format_scientific_context_with_data(self):
        inp = _make_input(
            scientific_summary="Good drug",
            unmet_need="CV risk",
            approved_therapies_json=json.dumps([{"drug_name": "insulin", "status": "approved"}]),
            clinical_pipeline_json=json.dumps([{"drug_name": "sema", "phase": "3", "status": "active"}]),
        )
        ctx = _format_scientific_context(inp)
        assert ctx["scientific_summary"] == "Good drug"
        assert "insulin" in ctx["approved_therapies"]
        assert "sema" in ctx["clinical_pipeline"]
