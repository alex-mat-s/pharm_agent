"""USPTO patent connector.

IMPORTANT: The legacy PatentsView endpoint (https://api.patentsview.org/patents/query)
is DEPRECATED and returns HTTP 301 redirects. Do NOT use it.

For USPTO patent search, use the USPTO Open Data Portal (ODP) API:
- https://developer.uspto.gov/api-catalog
- Requires USPTO_ODP_API_KEY environment variable

If USPTO_ODP_API_KEY is not configured, this connector returns source_unavailable
warning and the run continues without USPTO data.

Reference:
- USPTO Open Data Portal: https://developer.uspto.gov/
- PatentsView (deprecated): https://patentsview.org/
"""

from __future__ import annotations

import json
import logging

import httpx

from app.connectors.base import BaseConnector, _hash_payload, _now_iso
from app.schemas.evidence import (
    ConnectorQuery,
    ConnectorResult,
    EvidenceCategory,
    EvidenceItem,
    SourceRecord,
    SourceType,
)

logger = logging.getLogger("pharm_agent.connectors.uspto")

# DEPRECATED: Do NOT use this endpoint - it returns HTTP 301
# PATENTSVIEW_URL_DEPRECATED = "https://api.patentsview.org/patents/query"

# USPTO Open Data Portal API (requires API key)
USPTO_ODP_URL = "https://api.uspto.gov/patent/search/v1/patents"


class USPTOConnector(BaseConnector):
    """USPTO patent search connector.

    Uses USPTO Open Data Portal (ODP) API for patent search.
    Requires USPTO_ODP_API_KEY to be configured.
    
    The legacy PatentsView endpoint is deprecated and no longer supported.
    
    Error handling:
    - Missing API key: source_unavailable warning, run continues
    - 301 redirect: source_unavailable (deprecated endpoint detected)
    - 403: source_unavailable
    - 404: no_results
    """

    connector_name = "uspto"

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        super().__init__(http_client)
        self._api_key: str | None = None

    def _get_api_key(self) -> str | None:
        """Get USPTO ODP API key from config."""
        if self._api_key is None:
            from app.config import config
            self._api_key = config.uspto_odp_api_key
        return self._api_key

    def _search(self, query: ConnectorQuery) -> ConnectorResult:
        """Search USPTO patents.
        
        Returns source_unavailable warning if:
        - USPTO_ODP_API_KEY is not configured
        - API returns 301 (deprecated endpoint)
        - API returns 403 (forbidden)
        """
        # Check for API key
        api_key = self._get_api_key()
        if not api_key:
            logger.warning(
                "USPTO_ODP_API_KEY not configured. USPTO patent search unavailable."
            )
            return ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                warnings=[
                    "source_unavailable: USPTO_ODP_API_KEY not configured. "
                    "Set USPTO_ODP_API_KEY environment variable for USPTO patent search. "
                    "See https://developer.uspto.gov/ for API access."
                ],
            )

        http = self._get_http()
        search_term = self._pick_latin_name(query)

        # Build search query for USPTO ODP API
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        
        # Search in patent title and abstract
        search_payload = {
            "query": f"(title:({search_term}) OR abstract:({search_term}))",
            "fields": [
                "patentNumber",
                "title",
                "applicationDate",
                "grantDate",
                "inventors",
                "assignees",
                "abstract",
            ],
            "pagination": {
                "offset": 0,
                "limit": min(query.max_results, 25),
            },
            "sort": [{"field": "grantDate", "order": "desc"}],
        }

        try:
            resp = http.post(
                USPTO_ODP_URL,
                json=search_payload,
                headers=headers,
                timeout=30.0,
                follow_redirects=False,  # Detect 301 explicitly
            )
            status_code = resp.status_code
            
            # Handle 301 redirect (deprecated endpoint detection)
            if status_code == 301:
                logger.warning(
                    "USPTO API returned 301 redirect. "
                    "This indicates a deprecated endpoint or API change."
                )
                return ConnectorResult(
                    connector_name=self.connector_name,
                    query=query,
                    warnings=[
                        "source_unavailable: USPTO API returned 301 redirect. "
                        "The API endpoint may have changed. "
                        "Please check USPTO Open Data Portal for current endpoints."
                    ],
                )
            
            # Handle 403 Forbidden
            if status_code == 403:
                logger.warning("USPTO API returned 403 Forbidden.")
                return ConnectorResult(
                    connector_name=self.connector_name,
                    query=query,
                    warnings=[
                        "source_unavailable: USPTO API returned 403 Forbidden. "
                        "Check if USPTO_ODP_API_KEY is valid and not expired."
                    ],
                )
            
            # Handle 404 No Results
            if status_code == 404:
                logger.info("USPTO API returned 404 for query '%s'.", search_term)
                return ConnectorResult(
                    connector_name=self.connector_name,
                    query=query,
                    warnings=[f"no_results: No USPTO patents found for '{search_term}'."],
                )
            
            # Handle other errors
            if status_code >= 400:
                logger.warning(
                    "USPTO API error: %d %s",
                    status_code,
                    resp.text[:200] if resp.text else "No response body",
                )
                return ConnectorResult(
                    connector_name=self.connector_name,
                    query=query,
                    errors=[f"HTTP {status_code}: USPTO API error"],
                    warnings=[
                        f"source_unavailable: USPTO API returned HTTP {status_code}."
                    ],
                )

            resp.raise_for_status()
            data = resp.json()
            
        except httpx.HTTPStatusError as exc:
            logger.warning("USPTO API HTTP error: %s", exc)
            return ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                errors=[f"HTTP error: {exc}"],
                warnings=[
                    f"source_unavailable: USPTO API request failed. {exc}"
                ],
            )
        except Exception as exc:
            logger.warning("USPTO request failed: %s", exc)
            return ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                errors=[f"{type(exc).__name__}: {exc}"],
                warnings=[
                    f"source_unavailable: USPTO API request failed. {type(exc).__name__}"
                ],
            )

        # Parse results
        patents = data.get("results", data.get("patents", []))
        sources: list[SourceRecord] = []
        evidence: list[EvidenceItem] = []

        for pat in patents:
            patent_number = pat.get("patentNumber", "")
            if not patent_number:
                continue

            title = pat.get("title", "Untitled Patent")
            grant_date = pat.get("grantDate", "")
            application_date = pat.get("applicationDate", "")

            # Assignee info
            assignees = pat.get("assignees", [])
            assignee_names = []
            for a in assignees:
                if isinstance(a, dict):
                    org = a.get("organization", a.get("name", ""))
                    if org:
                        assignee_names.append(org)
                elif isinstance(a, str):
                    assignee_names.append(a)
            assignee_str = ", ".join(assignee_names) if assignee_names else "Unknown"

            source_id = f"uspto:{patent_number}"
            url = f"https://patents.google.com/patent/US{patent_number}"

            sources.append(
                SourceRecord(
                    source_id=source_id,
                    source_type=SourceType.uspto,
                    title=title,
                    url_or_path=url,
                    external_id=patent_number,
                    publisher=f"USPTO (Assignee: {assignee_str})" if assignee_str != "Unknown" else "USPTO",
                    publication_date=grant_date or application_date,
                    retrieved_at=_now_iso(),
                    query_used=f"title/abstract:{search_term}",
                    raw_payload_hash=_hash_payload(json.dumps(pat, sort_keys=True)),
                    citation_label=(
                        f"US Patent {patent_number}. {title}. "
                        f"Assignee: {assignee_str}. Granted: {grant_date}."
                    ),
                    evidence_summary=(
                        f"US patent {patent_number}: {title}. "
                        f"Assignee: {assignee_str}."
                    ),
                )
            )

            findings: list[str] = []
            if grant_date:
                findings.append(f"Grant date: {grant_date}")
            if application_date:
                findings.append(f"Application date: {application_date}")
            if assignee_str != "Unknown":
                findings.append(f"Assignee: {assignee_str}")

            evidence.append(
                EvidenceItem(
                    evidence_id=f"evi:{source_id}",
                    source_id=source_id,
                    category=EvidenceCategory.patent,
                    summary=f"US patent {patent_number}: {title}",
                    key_findings=findings,
                    confidence="medium",
                )
            )

        warnings: list[str] = []
        if not sources:
            warnings.append(
                f"no_results: No USPTO patents found for '{search_term}'. "
                "This does not mean no patents exist — try broader synonyms."
            )

        return ConnectorResult(
            connector_name=self.connector_name,
            query=query,
            sources=sources,
            evidence_items=evidence,
            total_results_available=len(sources),
            results_returned=len(sources),
            warnings=warnings,
        )
