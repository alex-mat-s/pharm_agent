"""WIPO Patentscope connector — global patent discovery.

IMPORTANT: WIPO Patentscope does NOT have a free public JSON REST API.
The result.jsf pages are web UI pages that return HTML and block scraping with 403.

This connector:
- Does NOT attempt to scrape PATENTSCOPE result.jsf pages
- Returns source_unavailable with helpful alternatives
- Treats 403 as source_unavailable (not fatal error)
- Allows the run to continue without WIPO data

For global patent search, consider these alternatives:
1. EPO OPS (free with registration) - https://www.epo.org/searching-for-patents/data/web-services/ops.html
2. USPTO Open Data Portal - https://developer.uspto.gov/
3. The Lens (free API with registration) - https://www.lens.org/
4. Google Patents (manual search) - https://patents.google.com/

Reference:
- WIPO PATENTSCOPE: https://patentscope.wipo.int/
- WIPO web services (SOAP, registration required): https://www.wipo.int/patentscope/en/webservice/
"""

from __future__ import annotations

import logging

import httpx

from app.connectors.base import BaseConnector, _now_iso
from app.schemas.evidence import (
    ConnectorQuery,
    ConnectorResult,
)

logger = logging.getLogger("pharm_agent.connectors.wipo")

# WIPO PATENTSCOPE web UI - DO NOT SCRAPE
# The result.jsf pages return HTML and block automated access with 403
# WIPO_PATENTSCOPE_UI = "https://patentscope.wipo.int/search/en/result.jsf"


class WIPOConnector(BaseConnector):
    """WIPO Patentscope — global patent discovery.

    IMPORTANT: This connector does NOT scrape WIPO web pages.
    WIPO Patentscope does not have a free public REST API.
    
    This connector returns a source_unavailable warning with recommendations
    for alternative patent sources (EPO OPS, USPTO, The Lens).
    
    Error handling:
    - No API available: returns source_unavailable (not fatal)
    - 403 Forbidden: returns source_unavailable (not fatal)
    - Run continues without WIPO data
    """

    connector_name = "wipo"

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        super().__init__(http_client)

    def _search(self, query: ConnectorQuery) -> ConnectorResult:
        """Return source_unavailable warning for WIPO.
        
        WIPO Patentscope does not have a free public REST API.
        This connector does NOT attempt to scrape web pages.
        
        The run continues without WIPO data - this is expected behavior.
        """
        search_term = self._pick_latin_name(query)
        
        # Return immediately with source_unavailable
        # Do NOT attempt to scrape PATENTSCOPE web UI
        logger.info(
            "WIPO connector: No public REST API available. "
            "Returning source_unavailable for query '%s'.",
            search_term,
        )
        
        return ConnectorResult(
            connector_name=self.connector_name,
            query=query,
            warnings=[
                "source_unavailable: WIPO Patentscope does not have a free public REST API. "
                "This is expected behavior - the run will continue without WIPO data. "
                "Alternatives: (1) EPO OPS for European/international patents (free with registration), "
                "(2) USPTO Open Data Portal for US patents, "
                "(3) The Lens (lens.org) for global patent search with free API. "
                f"Manual search: https://patentscope.wipo.int/search/en/search.jsf?query={search_term}"
            ],
        )

    def search_with_connectivity_check(
        self,
        query: ConnectorQuery,
        run_id: str = "unknown",
    ) -> ConnectorResult:
        """Search with optional connectivity check.
        
        This method can optionally check if WIPO is reachable, but will
        NOT attempt to parse HTML results.
        
        If the check returns 403, it returns source_unavailable.
        If the check returns 200, it still returns source_unavailable
        because we cannot parse HTML results.
        """
        http = self._get_http()
        search_term = self._pick_latin_name(query)
        
        # Check WIPO homepage connectivity (not scraping)
        try:
            resp = http.head(
                "https://patentscope.wipo.int/",
                timeout=10.0,
                follow_redirects=True,
            )
            status_code = resp.status_code
            
            if status_code == 403:
                logger.warning(
                    "WIPO returned 403 Forbidden. Access may be blocked."
                )
                return ConnectorResult(
                    connector_name=self.connector_name,
                    query=query,
                    warnings=[
                        "source_unavailable: WIPO Patentscope returned 403 Forbidden. "
                        "Access may be blocked for automated requests. "
                        "This is expected behavior - the run will continue without WIPO data. "
                        "Use EPO OPS or USPTO Open Data Portal instead."
                    ],
                )
            
        except httpx.TimeoutException:
            logger.warning("WIPO connectivity check timed out.")
            return ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                warnings=[
                    "source_unavailable: WIPO Patentscope connectivity check timed out. "
                    "The run will continue without WIPO data."
                ],
            )
        except Exception as exc:
            logger.warning("WIPO connectivity check failed: %s", exc)
            return ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                warnings=[
                    f"source_unavailable: WIPO Patentscope connectivity check failed: {type(exc).__name__}. "
                    "The run will continue without WIPO data."
                ],
            )
        
        # Even if WIPO is reachable, we cannot parse HTML results
        # Return source_unavailable with the standard message
        return self._search(query)
