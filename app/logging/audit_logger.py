from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.config import config
from app.schemas.audit import AuditEvent


def _redact(data: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets from metadata before writing to audit log."""
    redacted = {}
    secret_keys = {"api_key", "authorization", "bearer", "token", "password", "secret"}
    for key, value in data.items():
        if any(s in str(key).lower() for s in secret_keys):
            redacted[key] = "[REDACTED]"
        elif isinstance(value, dict):
            redacted[key] = _redact(value)
        else:
            redacted[key] = value
    return redacted


def log_event(event: AuditEvent) -> None:
    """Append a single audit event as one JSON line to the audit log."""
    config.ensure_dirs()
    audit_path = Path(config.logs_dir) / "audit.jsonl"
    # Redact secrets in metadata
    clean = event.model_copy(update={"metadata": _redact(event.metadata)})
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(clean.model_dump(mode="json"), ensure_ascii=False) + "\n")
