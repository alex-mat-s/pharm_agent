from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agents.intake_enrichment_agent import IntakeEnrichmentAgent
from app.schemas.input import RawInput
from app.schemas.intake_output import IntakeEnrichmentOutput
from app.schemas.pdf import PDFExtractionResult, PDFChunk


@pytest.fixture(autouse=True)
def _clear_audit_log(tmp_path, monkeypatch):
    audit_dir = tmp_path / "logs"
    audit_dir.mkdir()
    mock_config = MagicMock()
    mock_config.logs_dir = audit_dir
    mock_config.ensure_dirs = lambda: None
    monkeypatch.setattr("app.logging.audit_logger.config", mock_config)


class FakeStructuredLLMClient:
    """Fake structured LLM client that returns a valid IntakeEnrichmentOutput."""

    def __init__(self, return_value: IntakeEnrichmentOutput | None = None) -> None:
        self.return_value = return_value or IntakeEnrichmentOutput(
            normalized_inn={"preferred_name": "aspirin", "english_inn": "aspirin", "russian_name": "аспирин"},
            completeness="high",
        )
        self.calls: list[dict[str, Any]] = []

    def call(self, **kwargs: Any) -> IntakeEnrichmentOutput:
        self.calls.append(kwargs)
        return self.return_value

    def close(self) -> None:
        pass


def test_intake_agent_runs_pipeline():
    fake_client = FakeStructuredLLMClient()
    agent = IntakeEnrichmentAgent(client=fake_client)

    raw_input = RawInput(inn_raw="ацетилсалициловая кислота", disease_raw="инсульт")
    pdf_results = [
        PDFExtractionResult(
            pdf_id="source_1",
            sha256="abc123",
            page_count=10,
            chunks=[PDFChunk(pdf_id="source_1", page_number=1, text="Некоторый текст о лекарстве", char_count=27)],
        )
    ]

    result = agent.run(raw_input, pdf_results, run_id="run_001")

    assert isinstance(result, IntakeEnrichmentOutput)
    assert result.normalized_inn.preferred_name == "aspirin"
    assert result.completeness == "high"
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert "system_prompt" in call
    assert "user_prompt" in call
    assert call["run_id"] == "run_001"


def test_intake_agent_with_prompts_content():
    fake_client = FakeStructuredLLMClient()
    agent = IntakeEnrichmentAgent(client=fake_client)

    raw_input = RawInput(inn_raw="ацетилсалициловая кислота")
    pdf_results = [
        PDFExtractionResult(
            pdf_id="source_1",
            sha256="abc123",
            page_count=10,
            chunks=[PDFChunk(pdf_id="source_1", page_number=1, text="Некоторый текст", char_count=16)],
        )
    ]

    agent.run(raw_input, pdf_results, run_id="run_002")

    call = fake_client.calls[0]
    user_prompt = call["user_prompt"]
    # Verify the user prompt contains the input data
    assert "ацетилсалициловая кислота" in user_prompt
    assert "N/A" in user_prompt  # disease is optional, so N/A is used
    assert "abc123" in user_prompt


def test_intake_agent_completion_check():
    fake_client = FakeStructuredLLMClient()
    agent = IntakeEnrichmentAgent(client=fake_client)

    raw_input = RawInput(inn_raw="аспирин")
    pdf_results = [
        PDFExtractionResult(
            pdf_id="source_1",
            sha256="abc123",
            page_count=10,
            chunks=[PDFChunk(pdf_id="source_1", page_number=1, text="Некоторый текст", char_count=16)],
        )
    ]

    result = agent.run(raw_input, pdf_results, run_id="run_003")
    assert result.completeness == "high"
