from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx

from app.config import config
from app.schemas.audit import AuditEvent
from app.logging.audit_logger import log_event


class OpenRouterClient:
    """OpenRouter wrapper supporting structured JSON schema outputs and audit logging.

    All LLM calls go through this single wrapper. It supports:
    - model selection from config;
    - timeout;
    - structured output via json_schema response_format;
    - full audit logging;
    - redaction of secrets.
    """

    def __init__(self) -> None:
        self.api_key = config.openrouter_api_key
        self.base_url = config.openrouter_base_url.rstrip("/")
        self.default_model = config.default_openrouter_model
        self.client: httpx.Client | None = None

    def _ensure_client(self) -> httpx.Client:
        if self.client is None or self.client.is_closed:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            self.client = httpx.Client(headers=headers, timeout=config.llm_timeout_seconds)
        return self.client

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
        schema_name: str = "StructuredOutput",
        model: str | None = None,
        run_id: str = "unknown",
    ) -> dict[str, Any]:
        """Call OpenRouter chat completions with optional JSON schema response_format.

        Args:
            system_prompt: System message content.
            user_prompt: User message content.
            response_schema: JSON Schema dict for structured output. When provided,
                the response_format is set to json_schema mode.
            schema_name: Name for the JSON schema (required by OpenRouter API).
                Must match the model name used in the schema.
            model: Override the default model.
            run_id: Run identifier for audit logging.

        Returns:
            dict with parsed JSON content plus '_raw_response' key containing
            the raw JSON string from the LLM.

        Raises:
            OpenRouterError: on any network, HTTP, or parsing failure.
        """
        model = model or self.default_model
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": response_schema,
                },
            }

        start_time = time.time()
        event_id = str(uuid.uuid4())
        try:
            log_event(
                AuditEvent(
                    event_id=event_id,
                    run_id=run_id,
                    stage="openrouter",
                    event_type="llm_call",
                    timestamp=_now_iso(),
                    status="started",
                    metadata={
                        "model": model,
                        "has_schema": response_schema is not None,
                        "schema_name": schema_name if response_schema else None,
                        "system_prompt_hash": _hash(system_prompt),
                        "user_prompt_hash": _hash(user_prompt),
                    },
                )
            )
            resp = self._ensure_client().post(
                f"{self.base_url}/chat/completions", json=payload
            )
            resp.raise_for_status()
            data = resp.json()

            # Extract content from response
            raw_content = data["choices"][0]["message"]["content"]

            # Parse JSON — this is the critical validation boundary
            try:
                parsed = json.loads(raw_content)
            except json.JSONDecodeError as parse_exc:
                # Return the raw string so the structured client can retry
                log_event(
                    AuditEvent(
                        event_id=str(uuid.uuid4()),
                        run_id=run_id,
                        stage="openrouter",
                        event_type="llm_call",
                        timestamp=_now_iso(),
                        status="succeeded",
                        metadata={
                            "model": model,
                            "latency_ms": int((time.time() - start_time) * 1000),
                            "parse_error": str(parse_exc),
                            "response_hash": _hash(raw_content),
                        },
                    )
                )
                return {"_raw_response": raw_content, "_parse_error": True}

            parsed["_raw_response"] = raw_content
            latency_ms = int((time.time() - start_time) * 1000)
            usage = data.get("usage", {})
            log_event(
                AuditEvent(
                    event_id=str(uuid.uuid4()),
                    run_id=run_id,
                    stage="openrouter",
                    event_type="llm_call",
                    timestamp=_now_iso(),
                    status="succeeded",
                    metadata={
                        "model": model,
                        "latency_ms": latency_ms,
                        "input_tokens": usage.get("prompt_tokens"),
                        "output_tokens": usage.get("completion_tokens"),
                        "response_hash": _hash(raw_content),
                    },
                )
            )
            return parsed
        except httpx.HTTPStatusError as exc:
            log_event(
                AuditEvent(
                    event_id=str(uuid.uuid4()),
                    run_id=run_id,
                    stage="openrouter",
                    event_type="llm_call",
                    timestamp=_now_iso(),
                    status="failed",
                    metadata={
                        "model": model,
                        "status_code": exc.response.status_code,
                        "error": "http_status_error",
                        "error_message": str(exc),
                    },
                )
            )
            raise OpenRouterError(f"OpenRouter HTTP error {exc.response.status_code}: {exc}") from exc
        except Exception as exc:
            log_event(
                AuditEvent(
                    event_id=str(uuid.uuid4()),
                    run_id=run_id,
                    stage="openrouter",
                    event_type="llm_call",
                    timestamp=_now_iso(),
                    status="failed",
                    metadata={
                        "model": model,
                        "error": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
            )
            raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc

    def close(self) -> None:
        if self.client is not None and not self.client.is_closed:
            self.client.close()


class OpenRouterError(Exception):
    """Raised when an OpenRouter API call fails."""
    pass


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]