from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agents.intake_enrichment_agent import IntakeEnrichmentAgent
from app.schemas.input import RawInput
from app.schemas.intake_output import IntakeEnrichmentOutput


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
    """Test that intake agent runs without PDF context (PDFs analyzed in later stages)."""
    fake_client = FakeStructuredLLMClient()
    agent = IntakeEnrichmentAgent(client=fake_client)

    raw_input = RawInput(inn_raw="ацетилсалициловая кислота", disease_raw="инсульт")

    result = agent.run(raw_input, run_id="run_001")

    assert isinstance(result, IntakeEnrichmentOutput)
    assert result.normalized_inn.preferred_name == "aspirin"
    assert result.completeness == "high"
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert "system_prompt" in call
    assert "user_prompt" in call
    assert call["run_id"] == "run_001"


def test_intake_agent_with_prompts_content():
    """Test that user prompt contains raw input data but no PDF context."""
    fake_client = FakeStructuredLLMClient()
    agent = IntakeEnrichmentAgent(client=fake_client)

    raw_input = RawInput(inn_raw="ацетилсалициловая кислота")

    agent.run(raw_input, run_id="run_002")

    call = fake_client.calls[0]
    user_prompt = call["user_prompt"]
    # Verify the user prompt contains the input data
    assert "ацетилсалициловая кислота" in user_prompt
    assert "N/A" in user_prompt  # disease is optional, so N/A is used
    # Verify NO PDF context is included (PDFs are analyzed in later stages)
    assert "<pdf_evidence>" not in user_prompt
    assert "sha256" not in user_prompt


def test_intake_agent_completion_check():
    """Test that completeness flag is returned correctly."""
    fake_client = FakeStructuredLLMClient()
    agent = IntakeEnrichmentAgent(client=fake_client)

    raw_input = RawInput(inn_raw="аспирин")

    result = agent.run(raw_input, run_id="run_003")
    assert result.completeness == "high"


def test_intake_agent_with_all_optional_fields():
    """Test that all optional fields are passed correctly to the prompt."""
    fake_client = FakeStructuredLLMClient()
    agent = IntakeEnrichmentAgent(client=fake_client)

    raw_input = RawInput(
        inn_raw="метформин",
        disease_raw="сахарный диабет 2 типа",
        region="RU",
        molecule_type="small_molecule",
        stage="Phase III",
    )

    agent.run(raw_input, run_id="run_004")

    call = fake_client.calls[0]
    user_prompt = call["user_prompt"]
    assert "метформин" in user_prompt
    assert "сахарный диабет 2 типа" in user_prompt
    assert "RU" in user_prompt
    assert "small_molecule" in user_prompt
    assert "Phase III" in user_prompt
