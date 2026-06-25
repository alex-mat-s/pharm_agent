"""EAPO Patent Registry connector.

Eurasian Patent Organization (EAPO) registry for EA patents
that may be relevant to Russia and the Eurasian region.

EAPO provides:
- Registry of Eurasian patents
- Bulletin of publications
- Legal status information

Note: EAPO does not have a public REST API.
This connector provides guidance for manual verification.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.cache.patent_cache import PatentCache, make_cache_key
from app.config import config
from app.connectors.ru_patent.base_ru_connector import BaseRuPatentConnector
from app.schemas.ru_patent import (
    LegalStatus,
    PatentEvidence,
    PatentQuery,
    PatentSearchResult,
)

logger = logging.getLogger("pharm_agent.connectors.eapo")


def _now_iso() -> str:
    """Return current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).isoformat()


class EAPORegistryConnector(BaseRuPatentConnector):
    """EAPO Patent Registry connector.

    Eurasian patents that may be relevant to Russia and the Eurasian region.

    EAPO member states:
    - Armenia, Azerbaijan, Belarus, Kazakhstan, Kyrgyzstan,
      Russia, Tajikistan, Turkmenistan

    Note: EAPO does not provide a public REST API.
    This connector:
    - Provides URLs for manual search
    - Checks registry availability
    - Returns informational warnings

    Environment:
    - EAPO_BASE_URL (default: https://www.eapo.org)
    - EAPO_REGISTRY_URL (default: https://www.eapo.org/ru/publications/...)
    """

    connector_name = "eapo"
    jurisdiction = "EA"

    # EAPO member states
    MEMBER_STATES = ["AM", "AZ", "BY", "KZ", "KG", "RU", "TJ", "TM"]

    def __init__(
        self,
        http_client: httpx.Client | None = None,
        cache: PatentCache | None = None,
        use_cache: bool | None = None,
        base_url: str | None = None,
        registry_url: str | None = None,
    ) -> None:
        """Initialize EAPO Registry connector.

        Args:
            http_client: Optional HTTP client for testing.
            cache: Optional PatentCache instance.
            use_cache: Override config.patent_cache_enabled.
            base_url: Override config.eapo_base_url.
            registry_url: Override config.eapo_registry_url.
        """
        super().__init__(http_client, cache, use_cache)
        self._base_url = base_url or config.eapo_base_url
        self._registry_url = registry_url or config.eapo_registry_url

    def _get_cache(self) -> PatentCache | None:
        """Get or create cache instance."""
        if not self._use_cache:
            return None
        if self._cache is None:
            self._cache = PatentCache(
                config.eapo_patent_cache_dir,
                ttl_days=config.patent_cache_ttl_days,
            )
        return self._cache

    def _search_patents(
        self,
        query: PatentQuery,
        *,
        run_id: str = "unknown",
    ) -> PatentSearchResult:
        """Search EAPO registry for patents.

        EAPO does not provide a public REST API for search.
        This method provides guidance for manual search.
        """
        warnings: list[str] = []
        patents: list[PatentEvidence] = []

        search_terms = self.expand_query_terms(query)
        if not search_terms:
            return PatentSearchResult(
                connector_name=self.connector_name,
                query=query,
                warnings=["No search terms provided"],
                source_available=True,
            )

        # Check EAPO availability
        eapo_available = self._check_eapo_availability()

        if not eapo_available:
            warnings.append(
                "EAPO website appears to be unavailable. "
                "Try again later or use EPO OPS for Eurasian patents."
            )
            return PatentSearchResult(
                connector_name=self.connector_name,
                query=query,
                patents=[],
                warnings=warnings,
                source_available=False,
            )

        # Generate manual search URLs
        search_urls = self._generate_search_urls(search_terms)

        warnings.append(
            "EAPO does not provide a public REST API for automated patent search. "
            f"Manual search required at: {search_urls[0] if search_urls else self._registry_url}"
        )

        # Provide additional guidance
        warnings.append(
            "For Eurasian (EA) patents, also consider searching via EPO OPS "
            "using country code 'EA' or Espacenet."
        )

        return PatentSearchResult(
            connector_name=self.connector_name,
            query=query,
            patents=patents,
            total_results_available=0,
            results_returned=0,
            warnings=warnings,
            source_available=True,  # EAPO exists, just no API
            endpoint=self._base_url,
        )

    def _check_eapo_availability(self) -> bool:
        """Check if EAPO website is accessible."""
        try:
            http = self._get_http()
            resp = http.head(
                self._base_url,
                timeout=10.0,
                follow_redirects=True,
            )
            return resp.status_code < 500
        except Exception:
            return False

    def _generate_search_urls(self, terms: list[str]) -> list[str]:
        """Generate EAPO search URLs for manual search."""
        urls: list[str] = []

        for term in terms[:3]:
            encoded = quote_plus(term)
            # EAPO registry search URL (may change)
            urls.append(
                f"{self._registry_url}?search={encoded}"
            )

        return urls

    def _get_legal_status(
        self,
        document_number: str,
        *,
        run_id: str = "unknown",
    ) -> tuple[LegalStatus, list[str]]:
        """Get legal status from EAPO registry.

        Attempts to check status from EAPO registry.
        Falls back to providing manual verification guidance.
        """
        warnings: list[str] = []

        # Normalize document number
        doc_number = self._normalize_ea_number(document_number)
        number_only = self._extract_number(doc_number)

        # Check availability
        if not self._check_eapo_availability():
            warnings.append(
                "EAPO registry unavailable. "
                "Manual verification required when available."
            )
            return LegalStatus.unknown, warnings

        # Try to fetch status
        status, fetch_warnings = self._attempt_status_fetch(number_only)
        warnings.extend(fetch_warnings)

        # Provide verification URL
        registry_url = self._build_registry_url(number_only)
        warnings.append(
            f"Manual verification recommended: {registry_url}"
        )

        return status, warnings

    def _attempt_status_fetch(
        self,
        patent_number: str,
    ) -> tuple[LegalStatus, list[str]]:
        """Attempt to fetch status from EAPO registry."""
        warnings: list[str] = []

        url = self._build_registry_url(patent_number)

        try:
            http = self._get_http()
            resp = http.get(
                url,
                timeout=15.0,
                follow_redirects=True,
            )

            if resp.status_code == 404:
                warnings.append(
                    f"Patent EA{patent_number} not found in EAPO registry."
                )
                return LegalStatus.unknown, warnings

            if resp.status_code != 200:
                warnings.append(
                    f"EAPO registry returned status {resp.status_code}"
                )
                return LegalStatus.unknown, warnings

            # Try to parse status from HTML
            status = self._parse_status_from_html(resp.text)
            return status, warnings

        except httpx.TimeoutException:
            warnings.append("EAPO registry request timed out")
        except Exception as e:
            warnings.append(f"Failed to fetch from EAPO registry: {e}")

        return LegalStatus.unknown, warnings

    def _parse_status_from_html(self, html: str) -> LegalStatus:
        """Attempt to parse legal status from EAPO HTML response.

        This is fragile and may break if EAPO changes their page structure.
        """
        html_lower = html.lower()

        # Status patterns in Russian (EAPO uses Russian interface)
        status_patterns = {
            LegalStatus.active: [
                "действует",
                "патент действует",
                "valid",
                "in force",
            ],
            LegalStatus.expired: [
                "истек срок",
                "прекратил действие",
                "expired",
            ],
            LegalStatus.lapsed: [
                "утратил силу",
                "аннулирован",
                "lapsed",
            ],
            LegalStatus.pending: [
                "заявка",
                "pending",
                "examination",
            ],
        }

        for status, patterns in status_patterns.items():
            for pattern in patterns:
                if pattern in html_lower:
                    return status

        return LegalStatus.unknown

    def _normalize_ea_number(self, doc_number: str) -> str:
        """Normalize document number to EA format."""
        doc = doc_number.strip().upper()

        # If already has EA prefix, keep it
        if doc.startswith("EA"):
            return doc

        # If it's just a number, add EA prefix
        if doc.isdigit():
            return f"EA{doc}"

        return doc

    def _extract_number(self, doc_number: str) -> str:
        """Extract numeric part from document number."""
        number = doc_number.upper()
        number = re.sub(r"^EA", "", number)
        number = re.sub(r"[A-Z]+$", "", number)  # Remove trailing kind codes
        return number.strip()

    def _build_registry_url(self, patent_number: str) -> str:
        """Build URL for EAPO registry lookup."""
        return (
            f"{self._base_url}/ru/publications/publicat/"
            f"register_patent.php?id={quote_plus(patent_number)}"
        )

    def _make_patent_evidence(
        self,
        document_number: str,
        title: str,
        **kwargs,
    ) -> PatentEvidence:
        """Create a PatentEvidence object with EA jurisdiction."""
        # Call parent method but ensure jurisdiction is EA
        evidence = super()._make_patent_evidence(
            document_number=document_number,
            title=title,
            **kwargs,
        )
        evidence.jurisdiction = "EA"
        return evidence

    def get_member_states(self) -> list[str]:
        """Return list of EAPO member state codes."""
        return list(self.MEMBER_STATES)

    def is_relevant_for_russia(self) -> bool:
        """Check if this connector is relevant for Russia."""
        return "RU" in self.MEMBER_STATES
