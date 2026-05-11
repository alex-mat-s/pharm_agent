from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from app.llm.structured_client import StructuredLLMClient, StructuredOutputError
from app.llm.openrouter_client import OpenRouterError
from app.schemas.input import NormalizedINN
from app.schemas.intake_output import IntakeEnrichmentOutput


# ---------------------------------------------------------------------------
# Fake OpenRouter client for testing
# ---------------------------------------------------------------------------


class FakeOpenRouterClient:
    """Fake OpenRouter client that returns configurable responses per call.

    Supports returning pre-built dicts, raising OpenRouterError, or
    returning raw strings for parse-error testing.
    """

    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self.responses = responses
        self.call_index = 0
        self.call_log: list[dict[str, Any]] = []

    def call(self, **kwargs: Any) -> dict[str, Any]:
        self.call_log.append(kwargs)
        if self.call_index >= len(self.responses):
            raise RuntimeError(f"FakeOpenRouterClient: unexpected call #{self.call_index}")
        response = self.responses[self.call_index]
        self.call_index += 1
        if isinstance(response, Exception):
            raise response
        return response

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helper: build valid IntakeEnrichmentOutput response
# ---------------------------------------------------------------------------


def _valid_response() -> dict[str, Any]:
    """A valid response that matches IntakeEnrichmentOutput schema."""
    return {
        "normalized_inn": {"preferred_name": "aspirin"},
        "completeness": "high",
        "_raw_response": json.dumps({
            "normalized_inn": {"preferred_name": "aspirin"},
            "completeness": "high",
        }),
    }


def _invalid_json_response() -> dict[str, Any]:
    """Response where _raw_response is not valid JSON (parse error)."""
    return {
        "_raw_response": "not-json{{",
        "_parse_error": True,
    }


def _schema_mismatch_response() -> dict[str, Any]:
    """Response where JSON parses OK but fails Pydantic validation."""
    return {
        "normalized_inn": "this should be an object, not a string",
        "completeness": "high",
        "_raw_response": json.dumps({
            "normalized_inn": "this should be an object, not a string",
            "completeness": "high",
        }),
    }


def _repair_success_response() -> dict[str, Any]:
    """A valid response returned on the second (repair) attempt."""
    return {
        "normalized_inn": {"preferred_name": "aspirin"},
        "completeness": "high",
        "_raw_response": json.dumps({
            "normalized_inn": {"preferred_name": "aspirin"},
            "completeness": "high",
        }),
    }


def _repair_failure_response() -> dict[str, Any]:
    """Another invalid response returned on the repair attempt."""
    return {
        "normalized_inn": "still a string",
        "completeness": "high",
        "_raw_response": json.dumps({
            "normalized_inn": "still a string",
            "completeness": "high",
        }),
    }


# ---------------------------------------------------------------------------
# Fixture: redirect audit log
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_audit_log(tmp_path, monkeypatch):
    """Redirect audit log to a temporary directory so tests don't write to repo."""
    audit_dir = tmp_path / "logs"
    audit_dir.mkdir()
    mock_config = MagicMock()
    mock_config.logs_dir = audit_dir
    mock_config.ensure_dirs = lambda: None
    monkeypatch.setattr("app.logging.audit_logger.config", mock_config)


# ===================================================================
# TEST 1: Valid structured response passes validation
# ===================================================================


def test_valid_structured_response():
    """A valid JSON response matching the Pydantic schema should be returned directly."""
    fake = FakeOpenRouterClient(responses=[_valid_response()])
    client = StructuredLLMClient(client=fake)
    result = client.call(
        system_prompt="sys",
        user_prompt="user",
        output_model=IntakeEnrichmentOutput,
        run_id="run_001",
    )
    assert isinstance(result, IntakeEnrichmentOutput)
    assert result.normalized_inn.preferred_name == "aspirin"
    assert result.completeness == "high"
    # Only one call was needed
    assert fake.call_index == 1


# ===================================================================
# TEST 2: Invalid JSON triggers repair retry, then succeeds
# ===================================================================


def test_invalid_json_repair_success():
    """If the LLM returns invalid JSON, the repair retry should be attempted."""
    fake = FakeOpenRouterClient(responses=[_invalid_json_response(), _repair_success_response()])
    client = StructuredLLMClient(client=fake)
    result = client.call(
        system_prompt="sys",
        user_prompt="user",
        output_model=IntakeEnrichmentOutput,
        run_id="run_002",
    )
    assert isinstance(result, IntakeEnrichmentOutput)
    assert result.normalized_inn.preferred_name == "aspirin"
    # Both calls were made
    assert fake.call_index == 2
    # The repair prompt should include validation error info
    repair_call = fake.call_log[1]
    repair_user = repair_call["user_prompt"]
    assert "JSON parse error" in repair_user or "failed schema validation" in repair_user


# ===================================================================
# TEST 3: Schema mismatch triggers repair retry, then succeeds
# ===================================================================


def test_schema_mismatch_repair_success():
    """If the LLM returns valid JSON that fails Pydantic validation, repair retry happens."""
    fake = FakeOpenRouterClient(responses=[_schema_mismatch_response(), _repair_success_response()])
    client = StructuredLLMClient(client=fake)
    result = client.call(
        system_prompt="sys",
        user_prompt="user",
        output_model=IntakeEnrichmentOutput,
        run_id="run_003",
    )
    assert isinstance(result, IntakeEnrichmentOutput)
    assert fake.call_index == 2
    # The repair prompt should mention validation errors
    repair_call = fake.call_log[1]
    repair_user = repair_call["user_prompt"]
    assert "Validation errors" in repair_user or "validation" in repair_user.lower()


# ===================================================================
# TEST 4: Repair success after initial schema mismatch
# ===================================================================


def test_repair_success():
    """First attempt fails validation, second attempt succeeds."""
    fake = FakeOpenRouterClient(responses=[_schema_mismatch_response(), _repair_success_response()])
    client = StructuredLLMClient(client=fake)
    result = client.call(
        system_prompt="sys",
        user_prompt="user",
        output_model=IntakeEnrichmentOutput,
        run_id="run_004",
    )
    assert isinstance(result, IntakeEnrichmentOutput)
    assert result.normalized_inn.preferred_name == "aspirin"
    assert fake.call_index == 2


# ===================================================================
# TEST 5: Repair failure — both attempts fail validation
# ===================================================================


def test_repair_failure():
    """If both initial and repair attempts fail, StructuredOutputError is raised."""
    fake = FakeOpenRouterClient(responses=[_schema_mismatch_response(), _repair_failure_response()])
    client = StructuredLLMClient(client=fake)
    with pytest.raises(StructuredOutputError) as exc_info:
        client.call(
            system_prompt="sys",
            user_prompt="user",
            output_model=IntakeEnrichmentOutput,
            run_id="run_005",
        )
    assert "IntakeEnrichmentOutput" in str(exc_info.value)
    assert "run_005" in str(exc_info.value)
    assert fake.call_index == 2


# ===================================================================
# TEST 6: OpenRouter network failure raises StructuredOutputError
# ===================================================================


def test_openrouter_network_failure():
    """If OpenRouter raises an error, it should be wrapped in StructuredOutputError."""
    fake = FakeOpenRouterClient(responses=[OpenRouterError("timeout")])
    client = StructuredLLMClient(client=fake)
    with pytest.raises(StructuredOutputError) as exc_info:
        client.call(
            system_prompt="sys",
            user_prompt="user",
            output_model=IntakeEnrichmentOutput,
            run_id="run_006",
        )
    assert "timeout" in str(exc_info.value)


# ===================================================================
# TEST 7: Invalid JSON followed by invalid JSON = failure
# ===================================================================


def test_invalid_json_repair_also_invalid():
    """Both attempts return invalid JSON → StructuredOutputError."""
    fake = FakeOpenRouterClient(responses=[_invalid_json_response(), _invalid_json_response()])
    client = StructuredLLMClient(client=fake)
    with pytest.raises(StructuredOutputError):
        client.call(
            system_prompt="sys",
            user_prompt="user",
            output_model=IntakeEnrichmentOutput,
            run_id="run_007",
        )
    assert fake.call_index == 2


# ===================================================================
# TEST 8: Schema name is derived from output_model
# ===================================================================


def test_schema_name_derived_from_model():
    """The schema_name passed to OpenRouter should be derived from the output model class."""
    fake = FakeOpenRouterClient(responses=[_valid_response()])
    client = StructuredLLMClient(client=fake)
    client.call(
        system_prompt="sys",
        user_prompt="user",
        output_model=IntakeEnrichmentOutput,
        run_id="run_008",
    )
    call_kwargs = fake.call_log[0]
    assert call_kwargs["schema_name"] == "IntakeEnrichmentOutput"
    assert call_kwargs["response_schema"] is not None


# ===================================================================
# TEST 9: Repair prompt includes validation error details
# ===================================================================


def test_repair_prompt_includes_validation_error():
    """The repair prompt should include the validation error message and schema."""
    fake = FakeOpenRouterClient(responses=[_schema_mismatch_response(), _repair_success_response()])
    client = StructuredLLMClient(client=fake)
    client.call(
        system_prompt="sys",
        user_prompt="user",
        output_model=IntakeEnrichmentOutput,
        run_id="run_009",
    )
    # Second call is the repair attempt
    repair_call = fake.call_log[1]
    repair_prompt = repair_call["user_prompt"]
    # Should contain validation error details
    assert "Validation errors" in repair_prompt
    # Should contain the schema
    assert "IntakeEnrichmentOutput" in repair_prompt
    # Should contain the raw response
    assert "normalized_inn" in repair_prompt


# ===================================================================
# TEST 10: Works with a simple Pydantic model (not just IntakeEnrichmentOutput)
# ===================================================================


class SimpleOutput(BaseModel):
    """A simple model for testing generic structured output."""
    name: str
    value: int


def _valid_simple_response() -> dict[str, Any]:
    return {
        "name": "test",
        "value": 42,
        "_raw_response": json.dumps({"name": "test", "value": 42}),
    }


def _invalid_simple_response() -> dict[str, Any]:
    return {
        "name": "test",
        "value": "not_an_int",
        "_raw_response": json.dumps({"name": "test", "value": "not_an_int"}),
    }


def test_simple_model_valid():
    """Structured output works with any Pydantic model, not just IntakeEnrichmentOutput."""
    fake = FakeOpenRouterClient(responses=[_valid_simple_response()])
    client = StructuredLLMClient(client=fake)
    result = client.call(
        system_prompt="sys",
        user_prompt="user",
        output_model=SimpleOutput,
        run_id="run_010",
    )
    assert isinstance(result, SimpleOutput)
    assert result.name == "test"
    assert result.value == 42


def test_simple_model_repair_failure():
    """StructuredOutputError works with any Pydantic model."""
    fake = FakeOpenRouterClient(responses=[_invalid_simple_response(), _invalid_simple_response()])
    client = StructuredLLMClient(client=fake)
    with pytest.raises(StructuredOutputError) as exc_info:
        client.call(
            system_prompt="sys",
            user_prompt="user",
            output_model=SimpleOutput,
            run_id="run_011",
        )
    assert "SimpleOutput" in str(exc_info.value)


# ===================================================================
# TEST 11: Internal keys (_raw_response, _parse_error) stripped before validation
# ===================================================================


def test_internal_keys_stripped_before_validation():
    """The _raw_response and _parse_error keys must not cause Pydantic validation errors."""
    response_with_extras = _valid_response()
    # _raw_response is already in there; add _parse_error=False to verify stripping
    response_with_extras["_parse_error"] = False
    fake = FakeOpenRouterClient(responses=[response_with_extras])
    client = StructuredLLMClient(client=fake)
    result = client.call(
        system_prompt="sys",
        user_prompt="user",
        output_model=IntakeEnrichmentOutput,
        run_id="run_012",
    )
    assert isinstance(result, IntakeEnrichmentOutput)
    assert result.normalized_inn.preferred_name == "aspirin"


# ===================================================================
# TEST 12: Unvalidated output never leaks past StructuredLLMClient
# ===================================================================


def test_unvalidated_output_never_leaks():
    """If both attempts fail, no dict or partial model is ever returned — only StructuredOutputError."""
    fake = FakeOpenRouterClient(responses=[_schema_mismatch_response(), _repair_failure_response()])
    client = StructuredLLMClient(client=fake)
    with pytest.raises(StructuredOutputError):
        client.call(
            system_prompt="sys",
            user_prompt="user",
            output_model=IntakeEnrichmentOutput,
            run_id="run_013",
        )
    # Ensure we never got a partial result — the only return path is validated or exception


# ===================================================================
# TEST 13: Network error on retry also raises StructuredOutputError
# ===================================================================


def test_network_error_on_retry():
    """If the first call has a validation error and the retry has a network error, raise StructuredOutputError."""
    fake = FakeOpenRouterClient(responses=[_schema_mismatch_response(), OpenRouterError("connection reset")])
    client = StructuredLLMClient(client=fake)
    with pytest.raises(StructuredOutputError) as exc_info:
        client.call(
            system_prompt="sys",
            user_prompt="user",
            output_model=IntakeEnrichmentOutput,
            run_id="run_014",
        )
    assert "connection reset" in str(exc_info.value)