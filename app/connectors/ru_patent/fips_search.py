"""FIPS Information Search System connector.

Secondary source for Russian patent discovery.

FIPS (Federal Institute of Industrial Property) provides:
- Patent search at www.fips.ru
- Open registers for legal status

This connector provides search functionality.
Note: FIPS does not have a public REST API, so functionality is limited
to providing search guidance and URLs for manual verification.
"""

from __future__ import annotations

import logging
from urllib.parse import quote_plus

import httpx

from app.config import config
from app.connectors.ru_patent.base_ru_connector import BaseRuPatentConnector
from app.schemas.ru_patent import (
    LegalStatus,
    PatentEvidence,
    PatentQuery,
    PatentSearchResult,
)

logger = logging.getLogger("pharm_agent.connectors.fips")


class FIPSSearchConnector(BaseRuPatentConnector):
    """FIPS information search system connector.

    Secondary source for Russian patent discovery.

    FIPS does not provide a public REST API for patent search.
    This connector:
    - Provides URLs for manual search
    - Flags the need for manual verification
    - Returns informational warnings

    Environment:
    - FIPS_BASE_URL (default: https://www.fips.ru)
    """

    connector_name = "fips"
    jurisdiction = "RU"

    def __init__(
        self,
        http_client: httpx.Client | None = None,
        cache=None,
        use_cache: bool | None = None,
        base_url: str | None = None,
    ) -> None:
        """Initialize FIPS Search connector.

        Args:
            http_client: Optional HTTP client for testing.
            cache: Optional PatentCache instance.
            use_cache: Override config.patent_cache_enabled.
            base_url: Override config.fips_base_url.
        """
        super().__init__(http_client, cache, use_cache)
        self._base_url = base_url or config.fips_base_url

    def _search_patents(
        self,
        query: PatentQuery,
        *,
        run_id: str = "unknown",
    ) -> PatentSearchResult:
        """Search FIPS for patents.

        FIPS does not have a REST API for search.
        This method returns guidance for manual search.
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

        # Generate manual search URLs
        search_urls = self._generate_search_urls(search_terms)

        # Check if FIPS is accessible
        fips_available = self._check_fips_availability()

        if fips_available:
            warnings.append(
                "FIPS does not provide a public REST API for automated patent search. "
                f"Manual search required at: {search_urls[0] if search_urls else self._base_url}"
            )
        else:
            warnings.append(
                "FIPS website appears to be unavailable. "
                "Try again later or use Rospatent as primary source."
            )

        # Add all search URLs as guidance
        if len(search_urls) > 1:
            warnings.append(
                f"Alternative search URLs: {', '.join(search_urls[1:3])}"
            )

        return PatentSearchResult(
            connector_name=self.connector_name,
            query=query,
            patents=patents,
            total_results_available=0,
            results_returned=0,
            warnings=warnings,
            source_available=fips_available,
            endpoint=self._base_url,
        )

    def _check_fips_availability(self) -> bool:
        """Check if FIPS website is accessible."""
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
        """Generate FIPS search URLs for manual search.

        FIPS search pages:
        - https://www1.fips.ru/registers/all_registers/all_registers.php (registers)
        - https://www1.fips.ru/iiss/ (information search system)
        """
        urls: list[str] = []

        for term in terms[:3]:
            encoded = quote_plus(term)
            # Primary search URL - information search system
            urls.append(
                f"https://www1.fips.ru/iiss/search.xhtml?query={encoded}"
            )

        return urls

    def _get_legal_status(
        self,
        document_number: str,
        *,
        run_id: str = "unknown",
    ) -> tuple[LegalStatus, list[str]]:
        """Get legal status from FIPS.

        Delegates to FIPSRegistersConnector for actual lookup.
        Returns unknown with guidance for manual verification.
        """
        warnings: list[str] = []

        # Normalize document number
        doc_number = document_number.strip().upper()
        if not doc_number.startswith("RU"):
            doc_number = f"RU{doc_number}"

        # Generate verification URL
        register_url = self._get_register_url(doc_number)

        warnings.append(
            f"FIPS legal status lookup requires manual verification. "
            f"Check: {register_url}"
        )

        return LegalStatus.unknown, warnings

    def _get_register_url(self, document_number: str) -> str:
        """Get URL for patent register lookup."""
        # Extract number from RU prefix
        number = document_number.replace("RU", "").strip()
        return (
            f"https://new.fips.ru/registers/all_registers/all_registers.php"
            f"?doc_number={quote_plus(number)}"
        )
