from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import httpx

from app.config import config
from app.schemas.audit import AuditEvent
from app.logging.audit_logger import log_event

logger = logging.getLogger("pharm_agent.openrouter_client")

# Network-level errors that warrant an automatic retry
_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)


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

        max_retries = 3
        retry_delay = 5.0
        last_exc: Exception | None = None

        log_event(
            AuditEvent(
                event_id=str(uuid.uuid4()),
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

        for attempt in range(1, max_retries + 1):
            start_time = time.time()
            try:
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
                            "attempt": attempt,
                        },
                    )
                )
                return parsed

            except _RETRYABLE_ERRORS as exc:
                latency_ms = int((time.time() - start_time) * 1000)
                last_exc = exc
                if attempt < max_retries:
                    wait = retry_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "Network error on attempt %d/%d (run=%s): %s: %s. "
                        "Retrying in %.1fs...",
                        attempt, max_retries, run_id,
                        type(exc).__name__, exc, wait,
                    )
                    log_event(
                        AuditEvent(
                            event_id=str(uuid.uuid4()),
                            run_id=run_id,
                            stage="openrouter",
                            event_type="llm_call",
                            timestamp=_now_iso(),
                            status="retrying",
                            metadata={
                                "model": model,
                                "attempt": attempt,
                                "error": type(exc).__name__,
                                "error_message": str(exc)[:300],
                                "latency_ms": latency_ms,
                                "retry_delay_s": wait,
                            },
                        )
                    )
                    # Close stale connection before retry
                    self.close()
                    time.sleep(wait)
                    continue
                else:
                    logger.error(
                        "Network error after %d retries (run=%s): %s: %s",
                        max_retries, run_id, type(exc).__name__, exc,
                    )
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
                                "attempt": attempt,
                                "error": type(exc).__name__,
                                "error_message": str(exc)[:500],
                                "latency_ms": latency_ms,
                            },
                        )
                    )
                    raise OpenRouterError(
                        f"OpenRouter network error after {max_retries} retries: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc

            except httpx.HTTPStatusError as exc:
                latency_ms = int((time.time() - start_time) * 1000)
                # Retry on 429 (rate limit) and 5xx server errors
                if exc.response.status_code in (429, 502, 503, 504) and attempt < max_retries:
                    wait = retry_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "HTTP %d on attempt %d/%d (run=%s). Retrying in %.1fs...",
                        exc.response.status_code, attempt, max_retries, run_id, wait,
                    )
                    log_event(
                        AuditEvent(
                            event_id=str(uuid.uuid4()),
                            run_id=run_id,
                            stage="openrouter",
                            event_type="llm_call",
                            timestamp=_now_iso(),
                            status="retrying",
                            metadata={
                                "model": model,
                                "attempt": attempt,
                                "status_code": exc.response.status_code,
                                "latency_ms": latency_ms,
                            },
                        )
                    )
                    time.sleep(wait)
                    continue

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
                            "attempt": attempt,
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
                            "attempt": attempt,
                        },
                    )
                )
                raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc

        # Should not reach here, but safety net
        raise OpenRouterError(
            f"OpenRouter request failed after {max_retries} retries"
        ) from last_exc

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