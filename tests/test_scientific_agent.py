"""Tests for ScientificAgent with mocked LLM client."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.agents.scientific_agent import ScientificAgent
from app.llm.structured_client import StructuredOutputError
from app.schemas.evidence import (
    EvidenceCategory,
    EvidenceItem,
    SourceRecord,
    SourceType,
)
from app.schemas.scientific import ScientificAgentInput, ScientificAgentOutput


@pytest.fixture(autouse=True)
def _clear_audit_log(tmp_path, monkeypatch):
    mock_config = MagicMock()
    mock_config.logs_dir = tmp_path / "logs"
    mock_config.logs_dir.mkdir()
    mock_config.ensure_dirs = lambda: None
    monkeypatch.setattr("app.logging.audit_logger.config", mock_config)


class FakeStructuredLLMClient:
    def __init__(self, return_value=None, should_fail=False):
        self.return_value = return_value or ScientificAgentOutput(
            executive_summary="Aspirin has established evidence for stroke prevention.",
            evidence_gaps=["No Phase 3 data for this specific subpopulation"],
            source_ids_used=["pubmed:12345"],
            confidence="medium",
        )
        self.should_fail = should_fail
        self.calls: list = []

    def call(self, **kwargs):
        self.calls.append(kwargs)
        if self.should_fail:
            raise StructuredOutputError("Validation failed")
        return self.return_value


def _make_source() -> SourceRecord:
    return SourceRecord(
        source_id="pubmed:12345",
        source_type=SourceType.pubmed,
        title="Aspirin and Stroke",
        retrieved_at="2026-01-01T00:00:00+00:00",
        query_used="aspirin stroke",
        citation_label="Smith A. Aspirin and Stroke. J Med. 2024.",
    )


def _make_evidence() -> EvidenceItem:
    return EvidenceItem(
        evidence_id="evi:pubmed:12345",
        source_id="pubmed:12345",
        category=EvidenceCategory.clinical_trial,
        summary="Aspirin reduces stroke recurrence",
        confidence="high",
    )


def _make_input() -> ScientificAgentInput:
    return ScientificAgentInput(
        run_id="test_run",
        inn_preferred="aspirin",
        inn_english="acetylsalicylic acid",
        disease_preferred="stroke",
        pdf_hashes={"source_1": "abc123"},
        connector_coverage={"pubmed": "ok (1 results)"},
    )


def test_scientific_agent_happy_path():
    fake_llm = FakeStructuredLLMClient()
    agent = ScientificAgent(client=fake_llm)
    agent_input = _make_input()
    sources = [_make_source()]
    evidence = [_make_evidence()]

    result = agent.run(agent_input, sources, evidence)

    assert isinstance(result, ScientificAgentOutput)
    assert result.executive_summary
    assert len(fake_llm.calls) == 1
    assert "aspirin" in fake_llm.calls[0]["user_prompt"].lower()


def test_scientific_agent_failure():
    fake_llm = FakeStructuredLLMClient(should_fail=True)
    agent = ScientificAgent(client=fake_llm)

    with pytest.raises(StructuredOutputError):
        agent.run(_make_input(), [_make_source()], [_make_evidence()])


def test_scientific_agent_output_schema():
    output = ScientificAgentOutput(
        executive_summary="Test",
        confidence="high",
    )
    data = output.model_dump(mode="json")
    assert data["confidence"] == "high"
    assert "disclaimer" in data


def test_scientific_agent_prompt_includes_evidence():
    fake_llm = FakeStructuredLLMClient()
    agent = ScientificAgent(client=fake_llm)
    agent_input = _make_input()
    evidence = [_make_evidence()]
    sources = [_make_source()]

    agent.run(agent_input, sources, evidence)

    prompt = fake_llm.calls[0]["user_prompt"]
    assert "pubmed:12345" in prompt
    assert "Smith A" in prompt
