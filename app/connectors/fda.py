from __future__ import annotations

from datetime import UTC, datetime

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

OPENFDA_URL = "https://api.fda.gov/drug/drugsfda.json"


class FDAConnector(BaseConnector):
    connector_name = "fda"

    def _search(self, query: ConnectorQuery) -> ConnectorResult:
        http = self._get_http()
        latin_name = self._pick_latin_name(query)

        candidates = self._build_candidate_queries(latin_name, query)
        last_error: str | None = None

        for search_str in candidates:
            params: dict = {
                "search": search_str,
                "limit": str(min(query.max_results, 20)),
            }

            resp = http.get(OPENFDA_URL, params=params)

            if resp.status_code == 404:
                last_error = f"No FDA results for query: {search_str}"
                continue
            if resp.status_code == 403:
                last_error = f"FDA 403 Forbidden for query: {search_str}"
                continue

            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])

            if not results:
                last_error = f"Empty results for query: {search_str}"
                continue

            return self._parse_results(data, results, query, search_str)

        warnings = []
        if last_error:
            warnings.append(f"All FDA queries failed. Last: {last_error}")
        return ConnectorResult(
            connector_name=self.connector_name,
            query=query,
            warnings=warnings,
        )

    def _build_candidate_queries(
        self, latin_name: str, query: ConnectorQuery,
    ) -> list[str]:
        """Build a prioritized list of openFDA search strings."""
        seen: set[str] = set()
        candidates: list[str] = []

        primary = f'openfda.generic_name:"{latin_name}"'
        candidates.append(primary)
        seen.add(latin_name.lower())

        for brand in query.brand_names[:3]:
            if brand.lower() not in seen:
                candidates.append(f'openfda.brand_name:"{brand}"')
                seen.add(brand.lower())

        for syn in query.synonyms[:3]:
            low = syn.lower()
            if low not in seen:
                try:
                    syn.encode("ascii")
                except UnicodeEncodeError:
                    continue
                candidates.append(f'openfda.generic_name:"{syn}"')
                seen.add(low)

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
