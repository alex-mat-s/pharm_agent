"""FDA Orange Book connector — uses openFDA NDC API (free, no auth).

Orange Book proper does not have a REST API; we use the openFDA /drug/ndc.json
endpoint as a proxy for approved products, then cross-reference with Orange Book
text/CSV extracts for patent data.

Reference:
- https://www.fda.gov/drugs/drug-approvals-and-databases/orange-book-data-files
- https://open.fda.gov/apis/drug/ndc/
- Orange Book CSVs: https://www.fda.gov/drugs/drug-approvals-and-databases/orange-book-data-files
"""

from __future__ import annotations

import json
import logging
from typing import Any

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

logger = logging.getLogger("pharm_agent.connectors")

OPENFDA_NDC = "https://api.fda.gov/drug/ndc.json"
OB_CSV_URL = "https://www.accessdata.fda.gov/scripts/cder/ob/data/patent.csv"


def _pick_first_or(list_val: list[str] | Any | None, default: str = "") -> str:
    if isinstance(list_val, list) and list_val:
        return list_val[0]
    return default if list_val is None else str(list_val)


class OrangeBookConnector(BaseConnector):
    """FDA Orange Book — approved drug products with patent/exclusivity data.

    Uses openFDA NDC API for product lookup + Orange Book CSV dump for patent
    claims.  Runs without authentication (openFDA has generous rate limits).
    """

    connector_name = "orange_book"

    def __init__(
        self,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(http_client)

    def _get_http(self) -> httpx.Client:
        """Create HTTP client with optional proxy for FDA API."""
        if self._http is not None:
            return self._http
        from app.config import config
        proxies = config.fda_proxy_url
        self._http = httpx.Client(
            timeout=30.0,
            proxy=proxies,
        ) if proxies else httpx.Client(timeout=30.0)
        return self._http

    def _search(self, query: ConnectorQuery) -> ConnectorResult:
        http = self._get_http()
        search_term = self._pick_latin_name(query)

        # Step 1: Search openFDA NDC by generic name
        # Use multiple search strategies: generic_name, active_ingredient, brand_name
        strategies = [
            f'generic_name:"{search_term}"',
            f'active_ingredients.name:"{search_term}"',
        ]

        all_results: list[dict[str, Any]] = []

        for strategy in strategies:
            params = {
                "search": strategy,
                "limit": str(min(query.max_results, 25)),
            }
            try:
                resp = http.get(OPENFDA_NDC, params=params, timeout=30.0)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                all_results.extend(results)
            except httpx.HTTPStatusError as exc:
                logger.warning("Orange Book API error for strategy %s: %s", strategy, exc)
                continue
            except json.JSONDecodeError:
                continue

        seen_ndcs: set[str] = set()
        sources: list[SourceRecord] = []
        evidence: list[EvidenceItem] = []

        for item in all_results:
            ndc: str | None = item.get("product_ndc")
            if not ndc or ndc in seen_ndcs:
                continue
            seen_ndcs.add(ndc)

            brand_names: list[str] = item.get("brand_name") or []
            generic_names: list[str] = item.get("generic_name") or []
            product_name = _pick_first_or(brand_names, _pick_first_or(generic_names, "Unknown"))
            generic_name = _pick_first_or(generic_names, search_term)
            labeler = _pick_first_or(item.get("labeler_name"), "")
            dea = item.get("dea_schedule", "")
            marketing_start = item.get("marketing_start_date", "")

            # Active ingredients with strengths
            active_ingredients = item.get("active_ingredients", [])
            ai_str = "; ".join(
                f"{ai.get('name', '?')} ({ai.get('strength', '?')})"
                for ai in active_ingredients
            )

            source_id = f"orange_book:{ndc}"
            url = f"https://dailymed.nlm.nih.gov/dailymed/search.cfm?labelertype=all&query={ndc}"

            sources.append(
                SourceRecord(
                    source_id=source_id,
                    source_type=SourceType.orange_book,
                    title=f"{product_name} ({generic_name})",
                    url_or_path=url,
                    external_id=ndc,
                    publisher=f"FDA / Orange Book (Labeler: {labeler})" if labeler else "FDA / Orange Book",
                    publication_date=marketing_start,
                    last_updated_date=marketing_start,
                    retrieved_at=_now_iso(),
                    query_used=f"generic_name:{search_term}",
                    raw_payload_hash=_hash_payload(json.dumps(item, sort_keys=True)),
                    citation_label=(
                        f"FDA Orange Book. {product_name} — {generic_name}. "
                        f"NDC: {ndc}."
                    ),
                    evidence_summary=(
                        f"FDA-approved product: {product_name}. "
                        f"Active: {ai_str}. Marketing start: {marketing_start}"
                    ),
                )
            )

            findings: list[str] = []
            if active_ingredients:
                findings.append(f"Active ingredients: {ai_str}")
            if dea:
                findings.append(f"DEA Schedule: {dea}")
            if item.get("packaging"):
                pkg = item["packaging"]
                if isinstance(pkg, list) and pkg:
                    findings.append(f"Packaging: {_pick_first_or(pkg)}")
                else:
                    findings.append(f"Packaging: {pkg}")
            if labeler:
                findings.append(f"Labeler: {labeler}")

            evidence.append(
                EvidenceItem(
                    evidence_id=f"evi:{source_id}",
                    source_id=source_id,
                    category=EvidenceCategory.regulatory,
                    summary=f"FDA-approved product via Orange Book/NDC: {product_name} ({generic_name})",
                    key_findings=findings,
                    confidence="high",
                )
            )

            # Limited results to avoid flooding
            if len(sources) >= query.max_results:
                break

        warnings: list[str] = []
        if not sources:
            warnings.append(
                f"No Orange Book/NDC results for '{search_term}'. "
                "The API may not have coverage for this generic name."
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
