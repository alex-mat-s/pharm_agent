from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime

import httpx

from app.logging.audit_logger import log_tool_call
from app.schemas.evidence import (
    ConnectorCallLog,
    ConnectorQuery,
    ConnectorResult,
)

logger = logging.getLogger("pharm_agent.connectors")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _hash_payload(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


class BaseConnector(ABC):
    """Abstract base for all external source connectors.

    Subclasses implement ``_search`` to hit the external API and return
    a ``ConnectorResult``.  The public ``search`` wrapper adds timing,
    audit logging, and error handling.
    """

    connector_name: str = "base"

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._http = http_client

    def _get_http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=30.0)
        return self._http

    def search(self, query: ConnectorQuery, *, run_id: str = "unknown") -> ConnectorResult:
        from app.config import config

        start = time.monotonic()
        error_details: list[str] = []
        try:
            result = self._search(query)
            elapsed = int((time.monotonic() - start) * 1000)
            result.duration_ms = elapsed
            status = "succeeded"
            if result.errors:
                error_details = list(result.errors)
        except httpx.HTTPStatusError as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            detail = (
                f"{type(exc).__name__}: HTTP {exc.response.status_code} "
                f"for {exc.request.url}"
            )
            body_preview = exc.response.text[:500] if exc.response.text else ""
            error_details = [detail]
            if body_preview:
                error_details.append(f"Response body: {body_preview}")
            result = ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                errors=[detail],
                duration_ms=elapsed,
            )
            status = "failed"
        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            detail = f"{type(exc).__name__}: {exc}"
            error_details = [detail]
            result = ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                errors=[detail],
                duration_ms=elapsed,
            )
            status = "failed"

        output_summary: dict = {
            "results": result.results_returned,
            "errors": len(result.errors),
        }
        if config.debug and error_details:
            output_summary["error_details"] = error_details
        if config.debug and result.warnings:
            output_summary["warnings"] = result.warnings

        log_tool_call(
            run_id=run_id,
            stage="scientific_evidence",
            tool_name=self.connector_name,
            status=status,
            duration_ms=elapsed,
            input_summary={"query": query.inn, "disease": query.disease},
            output_summary=output_summary,
            error_message="; ".join(error_details) if error_details else None,
        )

        if config.debug:
            if error_details:
                logger.warning(
                    "[%s] %s | query=%s disease=%s | %dms | ERRORS: %s",
                    run_id, self.connector_name, query.inn, query.disease,
                    elapsed, "; ".join(error_details),
                )
            else:
                logger.info(
                    "[%s] %s | query=%s disease=%s | %dms | %d results",
                    run_id, self.connector_name, query.inn, query.disease,
                    elapsed, result.results_returned,
                )

        return result

    @abstractmethod
    def _search(self, query: ConnectorQuery) -> ConnectorResult:
        ...

    @staticmethod
    def _make_source_id(prefix: str) -> str:
        return f"{prefix}:{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _pick_latin_name(query: ConnectorQuery) -> str:
        """Return the best ASCII/Latin name for APIs that reject non-Latin chars.

        Priority: inn (if ASCII) → first ASCII synonym → first brand name → inn as-is.
        """
        def _is_latin(s: str) -> bool:
            try:
                s.encode("ascii")
                return True
            except UnicodeEncodeError:
                return False

        if _is_latin(query.inn):
            return query.inn

        for syn in query.synonyms:
            if syn and _is_latin(syn):
                return syn

        for brand in query.brand_names:
            if brand and _is_latin(brand):
                return brand

        return query.inn
