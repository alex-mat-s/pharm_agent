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

CTGOV_API = "https://clinicaltrials.gov/api/v2/studies"


class ClinicalTrialsConnector(BaseConnector):
    connector_name = "clinicaltrials"

    def _search(self, query: ConnectorQuery) -> ConnectorResult:
        http = self._get_http()

        terms: list[str] = []
        if query.inn:
            terms.append(query.inn)
        if query.disease:
            terms.append(query.disease)
        query_str = " AND ".join(terms) if terms else query.inn

        params: dict = {
            "query.cond": query.disease or "",
            "query.intr": query.inn,
            "pageSize": str(min(query.max_results, 50)),
            "format": "json",
        }

        resp = http.get(CTGOV_API, params=params)
        resp.raise_for_status()
        data = resp.json()

        studies = data.get("studies", [])
        total = data.get("totalCount", len(studies))

        sources: list[SourceRecord] = []
        evidence: list[EvidenceItem] = []

        for study in studies:
            proto = study.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            design = proto.get("designModule", {})
            sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
            conditions_mod = proto.get("conditionsModule", {})
            interventions_mod = proto.get("armsInterventionsModule", {})

            nct_id = ident.get("nctId", "")
            if not nct_id:
                continue

            brief_title = ident.get("briefTitle", "")
            official_title = ident.get("officialTitle", brief_title)
            overall_status = status_mod.get("overallStatus", "unknown")
            phases = design.get("phases", [])
            phase = ", ".join(phases) if phases else "N/A"
            sponsor = (sponsor_mod.get("leadSponsor") or {}).get("name", "")
            conditions = conditions_mod.get("conditions", [])
            interventions_raw = interventions_mod.get("interventions", [])
            interventions = [i.get("name", "") for i in interventions_raw]
            start_date = (status_mod.get("startDateStruct") or {}).get("date", "")

            source_id = f"ct:{nct_id}"
            sources.append(
                SourceRecord(
                    source_id=source_id,
                    source_type=SourceType.clinicaltrials,
                    title=brief_title,
                    url_or_path=f"https://clinicaltrials.gov/study/{nct_id}",
                    external_id=nct_id,
                    publisher=sponsor,
                    publication_date=start_date,
                    retrieved_at=_now_iso(),
                    query_used=query_str,
                    raw_payload_hash=_hash_payload(str(study)),
                    citation_label=f"{nct_id}: {brief_title} ({phase}, {overall_status})",
                    evidence_summary=f"Phase {phase}, status: {overall_status}",
                    reliability_notes=f"Conditions: {', '.join(conditions)}; Interventions: {', '.join(interventions)}",
                )
            )
            evidence.append(
                EvidenceItem(
                    evidence_id=f"evi:ct:{nct_id}",
                    source_id=source_id,
                    category=EvidenceCategory.clinical_trial,
                    summary=f"{brief_title} — Phase {phase}, {overall_status}",
                    key_findings=[
                        f"Phase: {phase}",
                        f"Status: {overall_status}",
                        f"Sponsor: {sponsor}",
                    ],
                    confidence="medium",
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
