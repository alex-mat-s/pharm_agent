from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import config
from app.schemas.audit import AuditEvent


# Central redaction rules — keys containing these sub-strings are scrubbed.
_SECRET_KEYWORDS: set[str] = {
    "api_key",
    "authorization",
    "auth",
    "bearer",
    "token",
    "password",
    "secret",
    "cookie",
    "session",
    "credential",
}


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return any(s in k for s in _SECRET_KEYWORDS)


def _redact(data: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets from metadata before writing to audit log or DB.

    Recursively walks dicts; replaces string/list values for secret keys
    with ``[REDACTED]``. Does NOT mutate the original dict.
    """
    redacted: dict[str, Any] = {}
    for key, value in data.items():
        if _is_secret_key(key):
            redacted[key] = "[REDACTED]"
        elif isinstance(value, dict):
            redacted[key] = _redact(value)
        elif isinstance(value, list):
            redacted[key] = [
                _redact(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            redacted[key] = value
    return redacted


def _hash_text(text: str) -> str:
    """Return a short SHA-256 hex digest for prompt / response tracking."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def log_event(event: AuditEvent) -> None:
    """Append a single audit event as one JSONL line.

    Also persists to SQLite when the database layer is accessible.
    """
    config.ensure_dirs()
    audit_path = Path(config.logs_dir) / "audit.jsonl"
    clean = event.model_copy(update={"metadata": _redact(event.metadata)})
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(clean.model_dump(mode="json"), ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Convenience helpers — build rich metadata and emit AuditEvent in one call.
# ---------------------------------------------------------------------------


def log_llm_call_started(
    *,
    run_id: str,
    stage: str,
    model: str,
    provider: str = "openrouter",
    schema_name: str | None = None,
    schema_version: str | None = None,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
) -> None:
    """Log that an LLM call has started."""
    log_event(
        AuditEvent(
            event_id=f"llm-start-{_now_iso()}",
            run_id=run_id,
            stage=stage,
            event_type="llm_call",
            timestamp=_now_iso(),
            status="started",
            metadata=_redact(
                {
                    "model": model,
                    "provider": provider,
                    "schema_name": schema_name,
                    "schema_version": schema_version,
                    "system_prompt_hash": _hash_text(system_prompt) if system_prompt else None,
                    "user_prompt_hash": _hash_text(user_prompt) if user_prompt else None,
                }
            ),
        )
    )


def log_llm_call_succeeded(
    *,
    run_id: str,
    stage: str,
    model: str,
    provider: str = "openrouter",
    raw_response: str,
    parsed_response: str | dict[str, Any] | None = None,
    validation_errors: str | None = None,
    retry_count: int = 0,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> None:
    """Log a successfully completed LLM call with all telemetry."""
    metadata: dict[str, Any] = {
        "model": model,
        "provider": provider,
        "raw_response": raw_response,
        "retry_count": retry_count,
    }
    if parsed_response is not None:
        metadata["parsed_response"] = parsed_response
    if validation_errors is not None:
        metadata["validation_errors"] = validation_errors
    if latency_ms is not None:
        metadata["latency_ms"] = latency_ms
    if input_tokens is not None:
        metadata["input_tokens"] = input_tokens
    if output_tokens is not None:
        metadata["output_tokens"] = output_tokens
    if cost_usd is not None:
        metadata["cost_usd"] = cost_usd
    if tool_calls is not None:
        metadata["tool_calls"] = tool_calls

    log_event(
        AuditEvent(
            event_id=f"llm-ok-{_now_iso()}",
            run_id=run_id,
            stage=stage,
            event_type="llm_call",
            timestamp=_now_iso(),
            status="succeeded",
            metadata=_redact(metadata),
        )
    )


def log_llm_call_failed(
    *,
    run_id: str,
    stage: str,
    model: str,
    provider: str = "openrouter",
    error_type: str,
    error_message: str,
    raw_response: str | None = None,
    retry_count: int = 0,
) -> None:
    """Log an LLM call failure."""
    metadata: dict[str, Any] = {
        "model": model,
        "provider": provider,
        "error_type": error_type,
        "error_message": error_message,
        "retry_count": retry_count,
    }
    if raw_response is not None:
        metadata["raw_response"] = raw_response

    log_event(
        AuditEvent(
            event_id=f"llm-fail-{_now_iso()}",
            run_id=run_id,
            stage=stage,
            event_type="llm_call",
            timestamp=_now_iso(),
            status="failed",
            metadata=_redact(metadata),
        )
    )


def log_tool_call(
    *,
    run_id: str,
    stage: str,
    tool_name: str,
    status: str,
    duration_ms: int | None = None,
    input_summary: dict[str, Any] | None = None,
    output_summary: dict[str, Any] | None = None,
    error_message: str | None = None,
    source_hash: str | None = None,
) -> None:
    """Log an important tool call (PDF hash, extraction, DB write, Obsidian write)."""
    metadata: dict[str, Any] = {
        "tool_name": tool_name,
    }
    if duration_ms is not None:
        metadata["duration_ms"] = duration_ms
    if input_summary is not None:
        metadata["input_summary"] = input_summary
    if output_summary is not None:
        metadata["output_summary"] = output_summary
    if error_message is not None:
        metadata["error_message"] = error_message
    if source_hash is not None:
        metadata["source_hash"] = source_hash

    log_event(
        AuditEvent(
            event_id=f"tool-{_now_iso()}",
            run_id=run_id,
            stage=stage,
            event_type="tool_call",
            timestamp=_now_iso(),
            status=status,  # type: ignore[arg-type]
            metadata=_redact(metadata),
        )
    )


def log_state_change(
    *,
    run_id: str,
    from_status: str,
    to_status: str,
    reason: str = "",
) -> None:
    """Log a run status transition."""
    log_event(
        AuditEvent(
            event_id=f"st-{_now_iso()}",
            run_id=run_id,
            stage="orchestrator",
            event_type="state_change",
            timestamp=_now_iso(),
            status="succeeded",
            metadata={
                "from_status": from_status,
                "to_status": to_status,
                "reason": reason,
            },
        )
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
