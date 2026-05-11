from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ValidationError

from app.llm.openrouter_client import OpenRouterClient, OpenRouterError
from app.logging.audit_logger import log_event
from app.schemas.audit import AuditEvent


class StructuredOutputError(Exception):
    """Raised when structured output cannot be produced after all retries.

    This error means the LLM response failed Pydantic validation on both
    the initial attempt and the repair retry. Unvalidated output must
    never propagate beyond this layer.
    """

    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StructuredLLMClient:
    """Wrapper around OpenRouterClient that enforces Pydantic schema validation.

    Guarantees:
    - Only validated Pydantic models are returned.
    - Unvalidated dicts NEVER leave this layer.
    - On validation failure, the full raw response and validation error are logged.
    - One repair retry is attempted with the validation error details included.
    - If repair also fails, StructuredOutputError is raised.
    """

    def __init__(self, client: OpenRouterClient | None = None) -> None:
        self.client = client or OpenRouterClient()

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[BaseModel],
        model: str | None = None,
        run_id: str = "unknown",
    ) -> BaseModel:
        """Call LLM with JSON schema, validate response, repair-retry on failure.

        Args:
            system_prompt: System message for the LLM.
            user_prompt: User message for the LLM.
            output_model: Pydantic model class for structured output validation.
            model: Override the default model.
            run_id: Run identifier for audit logging.

        Returns:
            Validated instance of output_model.

        Raises:
            StructuredOutputError: if both initial call and repair retry fail
                validation, or if the OpenRouter call itself fails.
        """
        schema = output_model.model_json_schema()
        schema_name = output_model.__name__

        # ---- Attempt 1: Initial call ----------------------------------------
        raw_dict = self._call_openrouter(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=schema,
            schema_name=schema_name,
            model=model,
            run_id=run_id,
            attempt=1,
        )
        validated, validation_error = self._validate(
            raw_dict=raw_dict,
            output_model=output_model,
            run_id=run_id,
            attempt=1,
        )
        if validated is not None:
            return validated

        # ---- Repair retry (Attempt 2) ---------------------------------------
        raw_response = raw_dict.get("_raw_response", json.dumps(raw_dict, default=str))
        repair_prompt = self._build_repair_prompt(
            raw_response=raw_response,
            validation_error=validation_error,
            schema=schema,
            schema_name=schema_name,
        )
        raw_dict2 = self._call_openrouter(
            system_prompt=system_prompt,
            user_prompt=repair_prompt,
            response_schema=schema,
            schema_name=schema_name,
            model=model,
            run_id=run_id,
            attempt=2,
        )
        validated2, _ = self._validate(
            raw_dict=raw_dict2,
            output_model=output_model,
            run_id=run_id,
            attempt=2,
        )
        if validated2 is not None:
            return validated2

        # ---- Both attempts failed ------------------------------------------
        raise StructuredOutputError(
            f"Structured output validation failed after initial call and repair retry. "
            f"Model: {output_model.__name__}, Run ID: {run_id}"
        )

    def _build_repair_prompt(
        self,
        raw_response: str,
        validation_error: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str:
        """Build a repair prompt that includes the validation error and schema."""
        schema_str = json.dumps(schema, indent=2, ensure_ascii=False)
        return (
            "Your previous JSON response failed schema validation.\n\n"
            f"Validation errors:\n{validation_error}\n\n"
            f"Raw response:\n{raw_response}\n\n"
            f"Required schema ({schema_name}):\n{schema_str}\n\n"
            "Please return a corrected JSON object that strictly follows the required schema. "
            "Return ONLY the JSON object, no additional text."
        )

    def _call_openrouter(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        schema_name: str,
        model: str | None,
        run_id: str,
        attempt: int,
    ) -> dict[str, Any]:
        """Call OpenRouter; on network failure raise StructuredOutputError."""
        try:
            return self.client.call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=response_schema,
                schema_name=schema_name,
                model=model,
                run_id=run_id,
            )
        except OpenRouterError as exc:
            log_event(
                AuditEvent(
                    event_id=f"llm_network_error_attempt_{attempt}",
                    run_id=run_id,
                    stage="structured_llm",
                    event_type="error",
                    timestamp=_now_iso(),
                    status="failed",
                    metadata={
                        "attempt": attempt,
                        "error": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
            )
            raise StructuredOutputError(
                f"OpenRouter call failed on attempt {attempt}: {exc}"
            ) from exc

    def _validate(
        self,
        raw_dict: dict[str, Any],
        output_model: type[BaseModel],
        run_id: str,
        attempt: int,
    ) -> tuple[BaseModel | None, str | None]:
        """Validate raw dict against Pydantic model.

        Returns:
            A tuple of (validated_model_or_None, validation_error_str_or_None).
            On success, validation_error is None.
            On failure, validated is None and validation_error contains the error details.
        """
        raw_content = raw_dict.get("_raw_response", json.dumps(raw_dict, default=str))
        has_parse_error = raw_dict.get("_parse_error", False)

        # Case 1: JSON parsing failed at the OpenRouter level
        if has_parse_error:
            error_msg = f"JSON parse error: response is not valid JSON"
            log_event(
                AuditEvent(
                    event_id=f"validation_error_attempt_{attempt}",
                    run_id=run_id,
                    stage="structured_llm",
                    event_type="error",
                    timestamp=_now_iso(),
                    status="failed",
                    metadata={
                        "attempt": attempt,
                        "error": "JSONDecodeError",
                        "error_message": error_msg,
                        "raw_response": raw_content,
                    },
                )
            )
            return None, error_msg

        # Case 2: JSON parsed OK, now validate against Pydantic schema
        try:
            # Remove internal keys before validation
            clean_dict = {
                k: v for k, v in raw_dict.items()
                if k not in ("_raw_response", "_parse_error")
            }
            validated = output_model.model_validate(clean_dict)
            # Log successful validation
            log_event(
                AuditEvent(
                    event_id=f"validation_success_attempt_{attempt}",
                    run_id=run_id,
                    stage="structured_llm",
                    event_type="tool_call",
                    timestamp=_now_iso(),
                    status="succeeded",
                    metadata={
                        "attempt": attempt,
                        "model": output_model.__name__,
                    },
                )
            )
            return validated, None
        except ValidationError as exc:
            error_msg = str(exc)
            log_event(
                AuditEvent(
                    event_id=f"validation_error_attempt_{attempt}",
                    run_id=run_id,
                    stage="structured_llm",
                    event_type="error",
                    timestamp=_now_iso(),
                    status="failed",
                    metadata={
                        "attempt": attempt,
                        "error": "ValidationError",
                        "error_message": error_msg,
                        "raw_response": raw_content,
                    },
                )
            )
            return None, error_msg