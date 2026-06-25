"""FIPS Open Registers connector.

Primary source for legal status verification of Russian patents.

FIPS Open Registers provide:
- Patent registration data
- Legal status information
- Document-by-number lookup

Note: FIPS registers do not have a structured REST API.
This connector provides guidance for manual verification.
"""

from __future__ import annotations

import logging
import re
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

logger = logging.getLogger("pharm_agent.connectors.fips_registers")


class FIPSRegistersConnector(BaseRuPatentConnector):
    """FIPS Open Registers connector.

    Primary source for legal status verification.
    Used for document-by-number lookup.

    Note: FIPS registers do not provide a public REST API.
    This connector:
    - Provides URLs for manual verification
    - Attempts to check basic availability
    - Returns informational warnings when lookup fails

    Environment:
    - FIPS_REGISTERS_BASE_URL (default: https://new.fips.ru/registers)
    """

    connector_name = "fips_registers"
    jurisdiction = "RU"

    def __init__(
        self,
        http_client: httpx.Client | None = None,
        cache=None,
        use_cache: bool | None = None,
        base_url: str | None = None,
    ) -> None:
        """Initialize FIPS Registers connector.

        Args:
            http_client: Optional HTTP client for testing.
            cache: Optional PatentCache instance.
            use_cache: Override config.patent_cache_enabled.
            base_url: Override config.fips_registers_base_url.
        """
        super().__init__(http_client, cache, use_cache)
        self._base_url = base_url or config.fips_registers_base_url

    def _search_patents(
        self,
        query: PatentQuery,
        *,
        run_id: str = "unknown",
    ) -> PatentSearchResult:
        """Search FIPS registers.

        FIPS registers are primarily for document-by-number lookup,
        not general search. Returns guidance for using proper search sources.
        """
        warnings: list[str] = []

        warnings.append(
            "FIPS registers are primarily for document-by-number verification. "
            "For patent search, use Rospatent Search Platform or FIPS Information Search System."
        )

        # Check availability
        available = self._check_registers_availability()

        if not available:
            warnings.append(
                "FIPS registers appear to be unavailable. "
                "Try again later."
            )

        return PatentSearchResult(
            connector_name=self.connector_name,
            query=query,
            patents=[],
            total_results_available=0,
            results_returned=0,
            warnings=warnings,
            source_available=available,
            endpoint=self._base_url,
        )

    def _check_registers_availability(self) -> bool:
        """Check if FIPS registers are accessible."""
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

    def verify_legal_status(
        self,
        patent_number: str,
        *,
        run_id: str = "unknown",
    ) -> tuple[LegalStatus, list[str]]:
        """Verify legal status for a specific patent number.

        This is the primary method for this connector.

        Args:
            patent_number: Russian patent number (e.g., "RU2123456" or "2123456").
            run_id: Run ID for audit logging.

        Returns:
            Tuple of (LegalStatus, list of warnings).
        """
        return self.get_legal_status(patent_number, run_id=run_id)

    def _get_legal_status(
        self,
        document_number: str,
        *,
        run_id: str = "unknown",
    ) -> tuple[LegalStatus, list[str]]:
        """Get legal status from FIPS registers.

        Attempts to fetch status information from FIPS registers.
        Falls back to providing manual verification guidance.
        """
        warnings: list[str] = []

        # Normalize document number
        doc_number = self._normalize_document_number(document_number)
        number_only = self._extract_number(doc_number)

        # Check availability first
        if not self._check_registers_availability():
            warnings.append(
                "FIPS registers unavailable. "
                "Manual verification required when available."
            )
            return LegalStatus.unknown, warnings

        # Try to fetch status from register page
        status, fetch_warnings = self._attempt_status_fetch(number_only)
        warnings.extend(fetch_warnings)

        # Always provide verification URL
        register_url = self._build_register_url(number_only)
        warnings.append(
            f"Manual verification recommended: {register_url}"
        )

        return status, warnings

    def _attempt_status_fetch(
        self,
        patent_number: str,
    ) -> tuple[LegalStatus, list[str]]:
        """Attempt to fetch status from FIPS registers.

        Note: This is a best-effort attempt. FIPS does not provide
        a structured API, so we try to parse HTML if possible.
        """
        warnings: list[str] = []

        # Build the register URL
        url = self._build_register_url(patent_number)

        try:
            http = self._get_http()
            resp = http.get(
                url,
                timeout=15.0,
                follow_redirects=True,
            )

            if resp.status_code == 404:
                warnings.append(
                    f"Patent {patent_number} not found in FIPS registers. "
                    "This may mean the patent does not exist or has different numbering."
                )
                return LegalStatus.unknown, warnings

            if resp.status_code != 200:
                warnings.append(
                    f"FIPS registers returned status {resp.status_code}"
                )
                return LegalStatus.unknown, warnings

            # Try to extract status from response
            # Note: This is fragile as FIPS doesn't provide structured data
            status = self._parse_status_from_html(resp.text)
            return status, warnings

        except httpx.TimeoutException:
            warnings.append("FIPS registers request timed out")
        except Exception as e:
            warnings.append(f"Failed to fetch from FIPS registers: {e}")

        return LegalStatus.unknown, warnings

    def _parse_status_from_html(self, html: str) -> LegalStatus:
        """Attempt to parse legal status from FIPS HTML response.

        This is fragile and may break if FIPS changes their page structure.
        Returns unknown if parsing fails.
        """
        html_lower = html.lower()

        # Look for status indicators in Russian
        # Common patterns on FIPS pages
        status_patterns = {
            LegalStatus.active: [
                "действует",
                "охранный документ действует",
                "патент действует",
                "в силе",
            ],
            LegalStatus.expired: [
                "прекратил действие",
                "срок действия истек",
                "не действует",
                "истек срок",
            ],
            LegalStatus.lapsed: [
                "аннулирован",
                "признан недействительным",
            ],
            LegalStatus.terminated: [
                "прекращено досрочно",
                "прекращение действия",
            ],
            LegalStatus.pending: [
                "заявка",
                "рассмотрение",
                "экспертиза",
            ],
            LegalStatus.withdrawn: [
                "отозван",
                "отзыв заявки",
            ],
            LegalStatus.rejected: [
                "отказ",
                "отклонена",
            ],
        }

        for status, patterns in status_patterns.items():
            for pattern in patterns:
                if pattern in html_lower:
                    logger.debug(
                        "Found status pattern '%s' indicating %s",
                        pattern,
                        status.value,
                    )
                    return status

        return LegalStatus.unknown

    def _normalize_document_number(self, doc_number: str) -> str:
        """Normalize document number to standard format."""
        doc = doc_number.strip().upper()

        # If already has RU prefix, keep it
        if doc.startswith("RU"):
            return doc

        # If it's just a number, add RU prefix
        if doc.isdigit():
            return f"RU{doc}"

        return doc

    def _extract_number(self, doc_number: str) -> str:
        """Extract numeric part from document number."""
        # Remove RU prefix and any kind indicators
        number = doc_number.upper()
        number = re.sub(r"^RU", "", number)
        number = re.sub(r"[A-Z]+$", "", number)  # Remove trailing letters (kind codes)
        return number.strip()

    def _build_register_url(self, patent_number: str) -> str:
        """Build URL for FIPS register lookup.

        FIPS has multiple register types:
        - Invention patents (RU)
        - Utility models
        - Industrial designs
        - Trademarks

        Default to invention patents.
        """
        # Primary register URL for invention patents
        return (
            f"{self._base_url}/all_registers/all_registers.php"
            f"?reg_number={quote_plus(patent_number)}&search_type=reg"
        )

    def get_verification_url(self, document_number: str) -> str:
        """Get URL for manual verification of a patent.

        Args:
            document_number: Patent document number.

        Returns:
            URL for FIPS register lookup.
        """
        number = self._extract_number(
            self._normalize_document_number(document_number)
        )
        return self._build_register_url(number)

    def batch_verify_status(
        self,
        document_numbers: list[str],
        *,
        run_id: str = "unknown",
    ) -> list[tuple[str, LegalStatus, list[str]]]:
        """Verify legal status for multiple patents.

        Args:
            document_numbers: List of patent numbers to verify.
            run_id: Run ID for audit logging.

        Returns:
            List of (document_number, status, warnings) tuples.
        """
        results: list[tuple[str, LegalStatus, list[str]]] = []

        for doc_num in document_numbers:
            status, warnings = self.get_legal_status(doc_num, run_id=run_id)
            results.append((doc_num, status, warnings))

        return results
