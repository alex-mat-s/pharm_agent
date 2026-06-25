from __future__ import annotations

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

logger = logging.getLogger("pharm_agent.connectors.fda")


class FDAConnector(BaseConnector):
    """openFDA NDC connector with fallback query strategy.
    
    Query priority:
    1. generic_name:<inn>
    2. active_ingredients.name:"<inn>"  (note: plural 'active_ingredients')
    3. active_ingredients.name:"<salt/synonym>"
    4. brand_name:<brand>
    
    Error handling:
    - 404: no_results (not fatal, tries next query)
    - 403: source_unavailable (stops iteration, returns warning)
    """
    
    connector_name = "fda"

    def _get_http(self) -> httpx.Client:
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
        from app.config import config
        http = self._get_http()
        latin_name = self._pick_latin_name(query)
        base_url = config.fda_api_url

        candidates = self._build_candidate_queries(latin_name, query)
        last_error: str | None = None
        fallback_attempt = 0
        source_unavailable = False

        for search_str in candidates:
            fallback_attempt += 1
            params: dict = {
                "search": search_str,
                "limit": str(min(query.max_results, 20)),
            }

            if config.fda_api_key:
                params["api_key"] = config.fda_api_key
            
            logger.debug(
                "FDA query attempt %d/%d: %s",
                fallback_attempt, len(candidates), search_str,
            )
            
            resp = http.get(base_url, params=params)
            status_code = resp.status_code

            logger.debug(
                "FDA response: status=%d, query=%s, fallback_attempt=%d",
                status_code, search_str, fallback_attempt,
            )

            if status_code == 404:
                # 404 = no_results for this query, try next fallback
                last_error = f"no_results: FDA 404 for query: {search_str}"
                logger.info(
                    "FDA 404 no_results for query '%s' (fallback %d/%d)",
                    search_str, fallback_attempt, len(candidates),
                )
                continue
            
            if status_code == 403:
                # 403 = source_unavailable, stop trying
                source_unavailable = True
                last_error = f"source_unavailable: FDA 403 Forbidden for query: {search_str}"
                logger.warning(
                    "FDA 403 source_unavailable for query '%s' (fallback %d/%d), stopping",
                    search_str, fallback_attempt, len(candidates),
                )
                break

            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])

            if not results:
                last_error = f"empty_results: No results for query: {search_str}"
                logger.info(
                    "FDA empty results for query '%s' (fallback %d/%d)",
                    search_str, fallback_attempt, len(candidates),
                )
                continue

            logger.info(
                "FDA success: %d results for query '%s' (fallback %d/%d)",
                len(results), search_str, fallback_attempt, len(candidates),
            )
            return self._parse_results(data, results, query, search_str)

        # All queries exhausted or source unavailable
        warnings = []
        if source_unavailable:
            warnings.append(f"source_unavailable: {last_error}")
        elif last_error:
            warnings.append(f"no_results: All FDA queries exhausted. Last: {last_error}")
        
        return ConnectorResult(
            connector_name=self.connector_name,
            query=query,
            warnings=warnings,
        )

    def _build_candidate_queries(
        self, latin_name: str, query: ConnectorQuery,
    ) -> list[str]:
        """Build a prioritized list of openFDA search strings.
        
        Priority order:
        1. generic_name:<inn> - preferred primary search
        2. active_ingredients.name:"<inn>" - NDC endpoint field (note: plural!)
        3. active_ingredients.name:"<salt/synonym>" - for salt forms and synonyms
        4. brand_name:<brand> - fallback for brand names
        
        Note: Uses 'active_ingredients' (plural) not 'active_ingredient' (singular).
        The openFDA NDC endpoint requires the plural form.
        """
        seen: set[str] = set()
        candidates: list[str] = []

        # 1. Primary: generic_name search (preferred)
        primary = f'openfda.generic_name:"{latin_name}"'
        candidates.append(primary)
        seen.add(latin_name.lower())

        # 2. Fallback: active_ingredients.name (NDC endpoint - plural form!)
        # Note: The correct field is 'active_ingredients' not 'active_ingredient'
        active_ing_query = f'active_ingredients.name:"{latin_name}"'
        candidates.append(active_ing_query)

        # 3. Salt forms and synonyms via active_ingredients.name
        for syn in query.synonyms[:3]:
            low = syn.lower()
            if low not in seen:
                try:
                    syn.encode("ascii")
                except UnicodeEncodeError:
                    continue
                # Use active_ingredients.name (plural) for synonyms/salts
                candidates.append(f'active_ingredients.name:"{syn}"')
                seen.add(low)

        # 4. Brand names as final fallback
        for brand in query.brand_names[:3]:
            if brand.lower() not in seen:
                candidates.append(f'openfda.brand_name:"{brand}"')
                seen.add(brand.lower())

        return candidates

    def _parse_results(
        self,
        data: dict,
        results: list[dict],
        query: ConnectorQuery,
        search_str: str,
    ) -> ConnectorResult:
        total = data.get("meta", {}).get("results", {}).get("total", len(results))

        sources: list[SourceRecord] = []
        evidence: list[EvidenceItem] = []

        for entry in results:
            app_no = entry.get("application_number", "")
            sponsor = entry.get("sponsor_name", "")
            products = entry.get("products", [])
            openfda = entry.get("openfda", {})
            generic_name = ", ".join(openfda.get("generic_name", []))
            brand_name = ", ".join(openfda.get("brand_name", []))

            submissions = entry.get("submissions", [])
            approval_date = ""
            for sub in submissions:
                if sub.get("submission_type") == "ORIG":
                    approval_date = sub.get("submission_status_date", "")
                    break

            source_id = f"fda:{app_no}" if app_no else self._make_source_id("fda")

            product_details: list[str] = []
            for p in products[:5]:
                dosage = p.get("dosage_form", "")
                route = p.get("route", "")
                # Note: products use 'active_ingredients' (plural)
                strength = (
                    p.get("active_ingredients", [{}])[0].get("strength", "")
                    if p.get("active_ingredients")
                    else ""
                )
                product_details.append(f"{dosage}, {route}, {strength}".strip(", "))

            title = f"FDA: {brand_name or generic_name} ({app_no})"
            sources.append(
                SourceRecord(
                    source_id=source_id,
                    source_type=SourceType.fda,
                    title=title,
                    url_or_path=(
                        f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm"
                        f"?event=overview.process&ApplNo={app_no}"
                        if app_no
                        else None
                    ),
                    external_id=app_no,
                    publisher="FDA",
                    publication_date=approval_date,
                    retrieved_at=_now_iso(),
                    query_used=search_str,
                    raw_payload_hash=_hash_payload(str(entry)),
                    citation_label=f"FDA {app_no}: {brand_name or generic_name}. Sponsor: {sponsor}.",
                    evidence_summary=f"Products: {'; '.join(product_details[:3])}",
                    reliability_notes=f"Sponsor: {sponsor}",
                )
            )
            evidence.append(
                EvidenceItem(
                    evidence_id=f"evi:fda:{app_no}" if app_no else self._make_source_id("evi:fda"),
                    source_id=source_id,
                    category=EvidenceCategory.regulatory,
                    summary=title,
                    key_findings=[
                        f"Application: {app_no}",
                        f"Sponsor: {sponsor}",
                        f"Brand: {brand_name}",
                        f"Generic: {generic_name}",
                    ],
                    confidence="high",
                )
            )

        return ConnectorResult(
            connector_name=self.connector_name,
            query=query,
            sources=sources,
            evidence_items=evidence,
            total_results_available=total,
            results_returned=len(sources),
        )
