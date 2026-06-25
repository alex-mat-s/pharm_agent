"""Rospatent Open Data / Open API connector.

Primary source for Russian patent discovery.

Rospatent provides:
- Search platform: https://searchplatform.rospatent.gov.ru
- Open API for patent search (requires API key for full access)
- Open data downloads

This connector attempts API search first, falls back gracefully if unavailable.
"""

from __future__ import annotations

import logging
import re
from typing import Any
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

logger = logging.getLogger("pharm_agent.connectors.rospatent")

# Rospatent Search Platform API endpoints
ROSPATENT_SEARCH_API = "/patsearch/v0.2"


class RospatentConnector(BaseRuPatentConnector):
    """Rospatent Open Data / Open API connector.

    Primary source for Russian patent discovery.

    Environment:
    - ROSPATENT_BASE_URL (default: https://searchplatform.rospatent.gov.ru)
    - ROSPATENT_API_KEY (optional, for extended API access)

    The connector gracefully handles missing credentials and API unavailability.
    """

    connector_name = "rospatent"
    jurisdiction = "RU"

    def __init__(
        self,
        http_client: httpx.Client | None = None,
        cache=None,
        use_cache: bool | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """Initialize Rospatent connector.

        Args:
            http_client: Optional HTTP client for testing.
            cache: Optional PatentCache instance.
            use_cache: Override config.patent_cache_enabled.
            api_key: Override config.rospatent_api_key.
            base_url: Override config.rospatent_base_url.
        """
        super().__init__(http_client, cache, use_cache)
        self._api_key = api_key if api_key is not None else config.rospatent_api_key
        self._base_url = base_url or config.rospatent_base_url

    def _has_api_key(self) -> bool:
        """Check if API key is configured."""
        return bool(self._api_key)

    def _search_patents(
        self,
        query: PatentQuery,
        *,
        run_id: str = "unknown",
    ) -> PatentSearchResult:
        """Search Rospatent for patents matching the query.

        Attempts API search if credentials available, otherwise returns
        informational result with manual search guidance.
        """
        warnings: list[str] = []
        patents: list[PatentEvidence] = []

        # Get search terms
        search_terms = self.expand_query_terms(query)
        if not search_terms:
            return PatentSearchResult(
                connector_name=self.connector_name,
                query=query,
                warnings=["No search terms provided"],
                source_available=True,
            )

        # Try API search if we have credentials
        if self._has_api_key():
            try:
                patents, api_warnings = self._search_via_api(search_terms, query.max_results)
                warnings.extend(api_warnings)
            except Exception as e:
                logger.warning("Rospatent API search failed: %s", e)
                warnings.append(f"Rospatent API search failed: {e}")
                # Fall through to return guidance
        else:
            warnings.append(
                "Rospatent API key not configured. "
                "Set ROSPATENT_API_KEY for full API access. "
                "For manual search, visit: https://searchplatform.rospatent.gov.ru"
            )

        # If no results from API, try alternative approach or provide guidance
        if not patents:
            # Generate manual search URLs for guidance
            manual_urls = self._generate_manual_search_urls(search_terms[:3])
            if manual_urls:
                warnings.append(
                    f"Manual search recommended. Try: {manual_urls[0]}"
                )

        return PatentSearchResult(
            connector_name=self.connector_name,
            query=query,
            patents=patents,
            total_results_available=len(patents),
            results_returned=len(patents),
            warnings=warnings,
            source_available=True,  # Source exists, even if no API access
            endpoint=self._base_url,
        )

    def _search_via_api(
        self,
        search_terms: list[str],
        max_results: int,
    ) -> tuple[list[PatentEvidence], list[str]]:
        """Search Rospatent via their API.

        Returns:
            Tuple of (list of PatentEvidence, list of warnings).
        """
        http = self._get_http()
        patents: list[PatentEvidence] = []
        warnings: list[str] = []

        # Build query string - combine terms with OR
        query_string = " OR ".join(f'"{term}"' for term in search_terms[:5])

        # Rospatent Search Platform API request
        # Documentation: https://searchplatform.rospatent.gov.ru/api-docs
        url = f"{self._base_url}{ROSPATENT_SEARCH_API}/search"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        payload = {
            "q": query_string,
            "limit": min(max_results, 50),
            "offset": 0,
            "sort": "relevance",
            # Note: "datasets" parameter is no longer supported by Rospatent API
            # Search is performed across all available datasets by default
        }

        try:
            resp = http.post(
                url,
                headers=headers,
                json=payload,
                timeout=30.0,
            )

            if resp.status_code == 401:
                warnings.append("Rospatent API: Invalid or expired API key")
                return patents, warnings

            if resp.status_code == 403:
                warnings.append("Rospatent API: Access forbidden - check API key permissions")
                return patents, warnings

            if resp.status_code == 404:
                warnings.append("Rospatent API endpoint not found - API may have changed")
                return patents, warnings

            if resp.status_code != 200:
                warnings.append(f"Rospatent API returned status {resp.status_code}")
                return patents, warnings

            data = resp.json()
            patents = self._parse_api_response(data)

        except httpx.TimeoutException:
            warnings.append("Rospatent API request timed out")
        except httpx.RequestError as e:
            warnings.append(f"Rospatent API request failed: {e}")
        except Exception as e:
            warnings.append(f"Failed to parse Rospatent API response: {e}")

        return patents, warnings

    def _parse_api_response(self, data: dict[str, Any]) -> list[PatentEvidence]:
        """Parse Rospatent API response into PatentEvidence objects."""
        patents: list[PatentEvidence] = []

        hits = data.get("hits", [])
        if not hits:
            hits = data.get("results", [])
        if not hits:
            hits = data.get("documents", [])

        for hit in hits:
            try:
                patent = self._parse_single_patent(hit)
                if patent:
                    patents.append(patent)
            except Exception as e:
                logger.debug("Failed to parse patent from API response: %s", e)
                continue

        return patents

    def _parse_single_patent(self, hit: dict[str, Any]) -> PatentEvidence | None:
        """Parse a single patent from API response.
        
        Handles the new Rospatent API structure with nested 'common', 'biblio', 'snippet' fields.
        """
        # New API structure has nested data
        common = hit.get("common", {})
        biblio = hit.get("biblio", {})
        snippet = hit.get("snippet", {})
        
        # Try to extract document ID from various locations
        doc_id = (
            hit.get("id")
            or common.get("document_number")
            or hit.get("document_id")
            or hit.get("publication_number")
        )
        if not doc_id:
            return None

        # Build proper document number with country code
        publishing_office = common.get("publishing_office", "")
        doc_number_raw = common.get("document_number", doc_id)
        kind = common.get("kind", "")
        
        # Format: OFFICE + NUMBER + KIND (e.g., "AU2018208699A1")
        if publishing_office and not str(doc_number_raw).startswith(publishing_office):
            doc_number = f"{publishing_office}{doc_number_raw}"
            if kind:
                doc_number = f"{doc_number}{kind}"
        else:
            doc_number = str(doc_number_raw)

        # Extract title from biblio (preferred) or snippet
        biblio_lang = biblio.get("en", biblio.get("ru", {}))
        title = (
            biblio_lang.get("title")
            or snippet.get("title", "").replace("<em>", "").replace("</em>", "")
            or hit.get("title")
            or f"Patent {doc_number}"
        )

        # Extract abstract/description from snippet
        abstract = snippet.get("description", "")
        if abstract:
            # Clean HTML tags
            abstract = abstract.replace("<em>", "").replace("</em>", "").replace("&#x2F;", "/")

        # Extract applicants from biblio
        applicants = self._extract_names_from_biblio(biblio_lang, "applicant")
        if not applicants:
            applicants = [snippet.get("applicant")] if snippet.get("applicant") else []
        
        # Extract patent holders
        patent_holders = self._extract_names_from_biblio(biblio_lang, "patentee")
        if not patent_holders:
            patent_holders = [snippet.get("patentee")] if snippet.get("patentee") else []
        
        # Extract inventors
        inventors = self._extract_names_from_biblio(biblio_lang, "inventor")
        if not inventors:
            inventors = [snippet.get("inventor")] if snippet.get("inventor") else []

        # Extract dates from common
        application = common.get("application", {})
        filing_date = application.get("filing_date")
        publication_date = common.get("publication_date")
        
        # Extract priority date from first priority entry
        priority_list = common.get("priority", [])
        priority_date = None
        if priority_list:
            for p in priority_list:
                if p.get("filing_date"):
                    priority_date = p.get("filing_date")
                    break

        # Extract IPC codes from classification
        classification = common.get("classification", {})
        ipc_codes = [c.get("fullname") for c in classification.get("ipc", []) if c.get("fullname")]
        cpc_codes = [c.get("fullname") for c in classification.get("cpc", []) if c.get("fullname")]

        # Build source URL
        source_url = f"https://searchplatform.rospatent.gov.ru/doc/{quote_plus(hit.get('id', doc_number))}"

        return self._make_patent_evidence(
            document_number=doc_number,
            title=title,
            application_number=application.get("number"),
            publication_number=f"{publishing_office}{common.get('document_number', '')}{kind}",
            abstract=abstract[:2000] if abstract else None,  # Limit abstract length
            applicants=applicants,
            patent_holders=patent_holders if patent_holders else applicants,
            inventors=inventors,
            filing_date=self._normalize_date(filing_date),
            priority_date=self._normalize_date(priority_date),
            publication_date=self._normalize_date(publication_date),
            grant_date=None,  # Not in new API response
            ipc_codes=ipc_codes,
            cpc_codes=cpc_codes,
            source_url=source_url,
            raw_metadata=hit,
        )
    
    def _extract_names_from_biblio(
        self,
        biblio_lang: dict[str, Any],
        field_name: str,
    ) -> list[str]:
        """Extract names from biblio structure (list of dicts with 'name' key)."""
        items = biblio_lang.get(field_name, [])
        if not items:
            return []
        if isinstance(items, list):
            return [item.get("name") for item in items if isinstance(item, dict) and item.get("name")]
        return []

    def _extract_list_field(
        self,
        data: dict[str, Any],
        field_names: list[str],
    ) -> list[str]:
        """Extract a list field from dict, trying multiple field names."""
        for name in field_names:
            value = data.get(name)
            if value:
                if isinstance(value, list):
                    return [str(v) for v in value if v]
                elif isinstance(value, str):
                    return [value]
        return []

    def _normalize_ru_patent_number(self, doc_id: str) -> str:
        """Normalize document ID to standard RU patent format."""
        # Remove whitespace
        doc_id = doc_id.strip()

        # If already has RU prefix, return as-is
        if doc_id.upper().startswith("RU"):
            return doc_id.upper()

        # If it's a pure number, add RU prefix
        if doc_id.isdigit():
            return f"RU{doc_id}"

        return doc_id

    def _normalize_date(self, date_value: Any) -> str | None:
        """Normalize date to YYYY-MM-DD format."""
        if not date_value:
            return None

        date_str = str(date_value)

        # Try common formats
        # YYYYMMDD -> YYYY-MM-DD
        if re.match(r"^\d{8}$", date_str):
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        # Already in YYYY-MM-DD format
        if re.match(r"^\d{4}-\d{2}-\d{2}", date_str):
            return date_str[:10]

        # DD.MM.YYYY -> YYYY-MM-DD
        match = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", date_str)
        if match:
            return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"

        return date_str

    def _generate_manual_search_urls(self, terms: list[str]) -> list[str]:
        """Generate manual search URLs for guidance."""
        urls: list[str] = []
        for term in terms[:2]:
            encoded = quote_plus(term)
            urls.append(
                f"https://searchplatform.rospatent.gov.ru/search?q={encoded}"
            )
        return urls

    def _get_legal_status(
        self,
        document_number: str,
        *,
        run_id: str = "unknown",
    ) -> tuple[LegalStatus, list[str]]:
        """Get legal status from Rospatent.

        Currently returns unknown with guidance for manual verification.
        Full implementation would require additional API endpoints.
        """
        warnings: list[str] = []

        # Normalize document number
        doc_number = self._normalize_ru_patent_number(document_number)

        if self._has_api_key():
            try:
                status, api_warnings = self._fetch_legal_status_api(doc_number)
                warnings.extend(api_warnings)
                return status, warnings
            except Exception as e:
                warnings.append(f"Legal status API lookup failed: {e}")

        # Provide guidance for manual verification
        warnings.append(
            f"Legal status for {doc_number} requires manual verification. "
            f"Check: https://new.fips.ru/registers/all_registers/all_registers.php"
        )

        return LegalStatus.unknown, warnings

    def _fetch_legal_status_api(
        self,
        document_number: str,
    ) -> tuple[LegalStatus, list[str]]:
        """Fetch legal status via API if available."""
        http = self._get_http()
        warnings: list[str] = []

        # Try to get document details which may include status
        url = f"{self._base_url}{ROSPATENT_SEARCH_API}/doc/{quote_plus(document_number)}"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }

        try:
            resp = http.get(url, headers=headers, timeout=30.0)

            if resp.status_code != 200:
                warnings.append(f"Document lookup returned status {resp.status_code}")
                return LegalStatus.unknown, warnings

            data = resp.json()

            # Try to extract status from response
            status_str = (
                data.get("legal_status")
                or data.get("status")
                or data.get("state")
            )

            if status_str:
                return self._parse_legal_status(status_str), warnings

        except Exception as e:
            warnings.append(f"Document lookup failed: {e}")

        return LegalStatus.unknown, warnings

    def _parse_legal_status(self, status_str: str) -> LegalStatus:
        """Parse legal status string to LegalStatus enum."""
        status_lower = status_str.lower()

        # Russian status terms
        if any(term in status_lower for term in ["действует", "активн", "active", "valid"]):
            return LegalStatus.active
        if any(term in status_lower for term in ["истек", "expired", "прекращен"]):
            return LegalStatus.expired
        if any(term in status_lower for term in ["аннулирован", "lapsed", "отозван"]):
            return LegalStatus.lapsed
        if any(term in status_lower for term in ["прекращено", "terminated"]):
            return LegalStatus.terminated
        if any(term in status_lower for term in ["рассмотрен", "pending", "заявка"]):
            return LegalStatus.pending
        if any(term in status_lower for term in ["отозван", "withdrawn"]):
            return LegalStatus.withdrawn
        if any(term in status_lower for term in ["отклонен", "rejected"]):
            return LegalStatus.rejected

        return LegalStatus.unknown
