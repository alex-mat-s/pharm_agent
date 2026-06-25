"""Base class for Russian and Eurasian patent connectors.

Extends BaseConnector with patent-specific methods:
- Query expansion (INN → synonyms, brands, targets)
- Legal status lookup
- Patent evidence normalization
"""

from __future__ import annotations

import logging
import time
from abc import abstractmethod
from datetime import datetime, timezone

import httpx

from app.cache.patent_cache import PatentCache, make_cache_key
from app.config import config
from app.connectors.base import BaseConnector
from app.logging.audit_logger import log_tool_call
from app.schemas.ru_patent import (
    LegalStatus,
    PatentEvidence,
    PatentQuery,
    PatentSearchResult,
)

logger = logging.getLogger("pharm_agent.connectors.ru_patent")


def _now_iso() -> str:
    """Return current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).isoformat()


class BaseRuPatentConnector(BaseConnector):
    """Base class for Russian/Eurasian patent connectors.

    Subclasses must implement:
    - _search_patents(): actual API call
    - _get_legal_status(): legal status lookup

    The public methods add caching, timing, audit logging, and error handling.
    """

    connector_name: str = "base_ru_patent"
    jurisdiction: str = "RU"

    def __init__(
        self,
        http_client: httpx.Client | None = None,
        cache: PatentCache | None = None,
        use_cache: bool | None = None,
    ) -> None:
        """Initialize the connector.

        Args:
            http_client: Optional HTTP client for testing.
            cache: Optional PatentCache instance.
            use_cache: Override config.patent_cache_enabled.
        """
        super().__init__(http_client)
        self._cache = cache
        self._use_cache = use_cache if use_cache is not None else config.patent_cache_enabled

    def _get_cache(self) -> PatentCache | None:
        """Get or create cache instance."""
        if not self._use_cache:
            return None
        if self._cache is None:
            self._cache = PatentCache(
                config.russian_patent_cache_dir,
                ttl_days=config.patent_cache_ttl_days,
            )
        return self._cache

    def search_patents(
        self,
        query: PatentQuery,
        *,
        run_id: str = "unknown",
    ) -> PatentSearchResult:
        """Search for patents matching the query.

        Handles caching, timing, logging, and error handling.

        Args:
            query: Patent search query.
            run_id: Run ID for audit logging.

        Returns:
            PatentSearchResult with found patents or errors/warnings.
        """
        start = time.monotonic()
        errors: list[str] = []
        warnings: list[str] = []

        # Check cache first
        cache = self._get_cache()
        if cache:
            cache_key = make_cache_key(
                self.connector_name,
                "search",
                query.inn,
                query.indication or "",
            )
            cached = cache.get(cache_key)
            if cached:
                logger.debug(
                    "[%s] Cache hit for %s search: %s",
                    run_id,
                    self.connector_name,
                    query.inn,
                )
                try:
                    result = PatentSearchResult(**cached)
                    result.duration_ms = 0
                    return result
                except Exception as e:
                    logger.warning("Failed to parse cached result: %s", e)

        # Make API call
        try:
            result = self._search_patents(query, run_id=run_id)
            elapsed = int((time.monotonic() - start) * 1000)
            result.duration_ms = elapsed

            # Cache successful results
            if cache and result.source_available and not result.errors:
                cache.set(cache_key, result.model_dump(mode="json"))

            status = "succeeded" if result.source_available else "partial"
            if result.errors:
                status = "failed"
                errors = list(result.errors)

        except httpx.HTTPStatusError as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            detail = (
                f"{type(exc).__name__}: HTTP {exc.response.status_code} "
                f"for {exc.request.url}"
            )
            errors.append(detail)
            result = PatentSearchResult(
                connector_name=self.connector_name,
                query=query,
                errors=errors,
                duration_ms=elapsed,
                source_available=False,
            )
            status = "failed"

        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            detail = f"{type(exc).__name__}: {exc}"
            errors.append(detail)
            result = PatentSearchResult(
                connector_name=self.connector_name,
                query=query,
                errors=errors,
                duration_ms=elapsed,
                source_available=False,
            )
            status = "failed"

        # Audit logging
        endpoint = getattr(self, "_base_url", None)
        log_tool_call(
            run_id=run_id,
            stage="patent_analysis",
            tool_name=self.connector_name,
            status=status,
            duration_ms=result.duration_ms,
            input_summary={
                "query": query.inn,
                "indication": query.indication,
                "terms_count": len(query.get_all_search_terms()),
            },
            output_summary={
                "results": result.results_returned,
                "errors": len(result.errors),
                "warnings": len(result.warnings),
                "source_available": result.source_available,
            },
            error_message="; ".join(errors) if errors else None,
            endpoint=endpoint,
        )

        if config.debug:
            if errors:
                logger.warning(
                    "[%s] %s | query=%s | %dms | ERRORS: %s",
                    run_id,
                    self.connector_name,
                    query.inn,
                    result.duration_ms,
                    "; ".join(errors),
                )
            else:
                logger.info(
                    "[%s] %s | query=%s | %dms | %d results",
                    run_id,
                    self.connector_name,
                    query.inn,
                    result.duration_ms,
                    result.results_returned,
                )

        return result

    @abstractmethod
    def _search_patents(
        self,
        query: PatentQuery,
        *,
        run_id: str = "unknown",
    ) -> PatentSearchResult:
        """Perform the actual patent search.

        Must be implemented by subclasses.
        """
        ...

    def get_legal_status(
        self,
        document_number: str,
        *,
        run_id: str = "unknown",
    ) -> tuple[LegalStatus, list[str]]:
        """Get legal status for a patent document.

        Args:
            document_number: Patent document number (e.g., "RU2123456").
            run_id: Run ID for audit logging.

        Returns:
            Tuple of (LegalStatus, list of warnings).
        """
        start = time.monotonic()
        warnings: list[str] = []

        # Check cache
        cache = self._get_cache()
        if cache:
            cache_key = make_cache_key(
                self.connector_name,
                "status",
                document_number,
            )
            cached = cache.get(cache_key)
            if cached:
                logger.debug(
                    "[%s] Cache hit for %s status: %s",
                    run_id,
                    self.connector_name,
                    document_number,
                )
                try:
                    return LegalStatus(cached.get("status", "unknown")), cached.get("warnings", [])
                except Exception:
                    pass

        try:
            status, warnings = self._get_legal_status(document_number, run_id=run_id)
            elapsed = int((time.monotonic() - start) * 1000)

            # Cache result
            if cache:
                cache.set(cache_key, {
                    "status": status.value,
                    "warnings": warnings,
                    "document_number": document_number,
                })

            log_tool_call(
                run_id=run_id,
                stage="patent_analysis",
                tool_name=f"{self.connector_name}_status",
                status="succeeded",
                duration_ms=elapsed,
                input_summary={"document_number": document_number},
                output_summary={"legal_status": status.value, "warnings_count": len(warnings)},
            )

            return status, warnings

        except Exception as exc:
            elapsed = int((time.monotonic() - start) * 1000)
            warning = f"Failed to get legal status for {document_number}: {exc}"
            warnings.append(warning)

            log_tool_call(
                run_id=run_id,
                stage="patent_analysis",
                tool_name=f"{self.connector_name}_status",
                status="failed",
                duration_ms=elapsed,
                input_summary={"document_number": document_number},
                output_summary={"legal_status": "unknown"},
                error_message=str(exc),
            )

            return LegalStatus.unknown, warnings

    def _search(self, query) -> ConnectorResult:
        """Dummy implementation to satisfy BaseConnector ABC.

        Russian patent connectors use search_patents() instead.
        """
        raise NotImplementedError("Use search_patents() for Russian patent connectors")

    def _get_legal_status(
        self,
        document_number: str,
        *,
        run_id: str = "unknown",
    ) -> tuple[LegalStatus, list[str]]:
        """Perform the actual legal status lookup.

        Default implementation returns unknown status.
        Subclasses should override for actual lookups.
        """
        return LegalStatus.unknown, [
            f"Legal status lookup not implemented for {self.connector_name}"
        ]

    def expand_query_terms(self, query: PatentQuery) -> list[str]:
        """Expand query into multiple search terms.

        Args:
            query: Patent query with INN, synonyms, etc.

        Returns:
            List of unique search terms.
        """
        return query.get_all_search_terms()

    def _make_patent_evidence(
        self,
        document_number: str,
        title: str,
        *,
        application_number: str | None = None,
        publication_number: str | None = None,
        abstract: str | None = None,
        applicants: list[str] | None = None,
        patent_holders: list[str] | None = None,
        inventors: list[str] | None = None,
        filing_date: str | None = None,
        priority_date: str | None = None,
        publication_date: str | None = None,
        grant_date: str | None = None,
        legal_status: LegalStatus = LegalStatus.unknown,
        ipc_codes: list[str] | None = None,
        cpc_codes: list[str] | None = None,
        source_url: str | None = None,
        raw_metadata: dict | None = None,
        warnings: list[str] | None = None,
    ) -> PatentEvidence:
        """Create a normalized PatentEvidence object.

        Helper method for consistent evidence creation across connectors.
        """
        return PatentEvidence(
            source_id=f"{self.connector_name}:{document_number}",
            source_type=self.connector_name,
            jurisdiction=self.jurisdiction,
            document_number=document_number,
            application_number=application_number,
            publication_number=publication_number,
            title=title,
            abstract=abstract,
            applicants=applicants or [],
            patent_holders=patent_holders or [],
            inventors=inventors or [],
            filing_date=filing_date,
            priority_date=priority_date,
            publication_date=publication_date,
            grant_date=grant_date,
            legal_status=legal_status,
            ipc_codes=ipc_codes or [],
            cpc_codes=cpc_codes or [],
            source_url=source_url,
            retrieved_at=_now_iso(),
            raw_metadata=raw_metadata or {},
            warnings=warnings or [],
        )
