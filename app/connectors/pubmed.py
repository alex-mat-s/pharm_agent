from __future__ import annotations

import uuid
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

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

_PUBTYPE_TO_CATEGORY: dict[str, EvidenceCategory] = {
    "clinical trial": EvidenceCategory.clinical_trial,
    "randomized controlled trial": EvidenceCategory.clinical_trial,
    "controlled clinical trial": EvidenceCategory.clinical_trial,
    "clinical trial, phase i": EvidenceCategory.clinical_trial,
    "clinical trial, phase ii": EvidenceCategory.clinical_trial,
    "clinical trial, phase iii": EvidenceCategory.clinical_trial,
    "clinical trial, phase iv": EvidenceCategory.clinical_trial,
    "meta-analysis": EvidenceCategory.review,
    "systematic review": EvidenceCategory.review,
    "review": EvidenceCategory.review,
    "practice guideline": EvidenceCategory.guideline,
    "guideline": EvidenceCategory.guideline,
    "case reports": EvidenceCategory.other,
    "observational study": EvidenceCategory.epidemiology,
    "comparative study": EvidenceCategory.clinical_trial,
}


def _classify_pubtype(pubtypes: list[str]) -> EvidenceCategory:
    """Map PubMed publication types to EvidenceCategory."""
    for pt in pubtypes:
        cat = _PUBTYPE_TO_CATEGORY.get(pt.lower())
        if cat is not None:
            return cat
    return EvidenceCategory.other


def _build_strict_query(query: ConnectorQuery) -> str:
    """INN AND disease — precise query."""
    parts = [query.inn]
    if query.disease:
        parts.append(query.disease)
    return " AND ".join(f'"{p}"' for p in parts)


def _build_broad_query(query: ConnectorQuery) -> str:
    """INN OR synonyms — fallback when strict returns 0."""
    terms = [query.inn] + list(query.synonyms[:3])
    return " OR ".join(f'"{t}"' for t in terms)


class PubMedConnector(BaseConnector):
    connector_name = "pubmed"

    def __init__(
        self,
        http_client: httpx.Client | None = None,
        api_key: str | None = None,
    ) -> None:
        super().__init__(http_client)
        self._api_key = api_key

    def _search(self, query: ConnectorQuery) -> ConnectorResult:
        q = _build_strict_query(query)
        result = self._run_search(query, q)

        if result.results_returned == 0 and (query.disease or query.synonyms):
            broad_q = _build_broad_query(query)
            result = self._run_search(query, broad_q)
            if result.results_returned > 0:
                result.warnings.append(
                    f"Strict query returned 0 results; broadened to: {broad_q}"
                )

        return result

    def _run_search(self, query: ConnectorQuery, q: str) -> ConnectorResult:
        http = self._get_http()

        esearch_params: dict = {
            "db": "pubmed",
            "term": q,
            "retmax": str(query.max_results),
            "retmode": "json",
        }
        if self._api_key:
            esearch_params["api_key"] = self._api_key

        resp = http.get(ESEARCH_URL, params=esearch_params)
        resp.raise_for_status()
        search_data = resp.json()

        id_list = search_data.get("esearchresult", {}).get("idlist", [])
        total = int(search_data.get("esearchresult", {}).get("count", 0))

        if not id_list:
            return ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                total_results_available=total,
                warnings=["No PubMed results found"] if total == 0 else [],
            )

        summary_params: dict = {
            "db": "pubmed",
            "id": ",".join(id_list),
            "retmode": "json",
        }
        if self._api_key:
            summary_params["api_key"] = self._api_key

        resp2 = http.get(ESUMMARY_URL, params=summary_params)
        resp2.raise_for_status()
        summary_data = resp2.json()

        abstracts = self._fetch_abstracts(http, id_list)

        sources: list[SourceRecord] = []
        evidence: list[EvidenceItem] = []
        result_entries = summary_data.get("result", {})

        for pmid in id_list:
            entry = result_entries.get(pmid)
            if not entry or not isinstance(entry, dict):
                continue

            source_id = f"pubmed:{pmid}"
            title = entry.get("title", "")
            journal = entry.get("fulljournalname", entry.get("source", ""))
            pub_date = entry.get("pubdate", "")
            authors = entry.get("sortfirstauthor", "")
            pubtypes = [
                pt.get("value", "") if isinstance(pt, dict) else str(pt)
                for pt in entry.get("pubtype", [])
            ]
            category = _classify_pubtype(pubtypes)
            abstract = abstracts.get(pmid, "")

            evidence_summary = abstract[:500] if abstract else title

            sources.append(
                SourceRecord(
                    source_id=source_id,
                    source_type=SourceType.pubmed,
                    title=title,
                    url_or_path=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    external_id=pmid,
                    publisher=journal,
                    publication_date=pub_date,
                    retrieved_at=_now_iso(),
                    query_used=q,
                    raw_payload_hash=_hash_payload(str(entry)),
                    citation_label=f"{authors}. {title}. {journal}. {pub_date}.",
                    evidence_summary=evidence_summary,
                )
            )
            evidence.append(
                EvidenceItem(
                    evidence_id=f"evi:pubmed:{pmid}",
                    source_id=source_id,
                    category=category,
                    summary=evidence_summary,
                    key_findings=[f"Type: {', '.join(pubtypes)}"] if pubtypes else [],
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

    def _fetch_abstracts(self, http: httpx.Client, pmids: list[str]) -> dict[str, str]:
        """Best-effort EFetch for abstracts. Returns {pmid: abstract_text}."""
        if not pmids:
            return {}
        try:
            params: dict = {
                "db": "pubmed",
                "id": ",".join(pmids),
                "rettype": "abstract",
                "retmode": "xml",
            }
            if self._api_key:
                params["api_key"] = self._api_key

            resp = http.get(EFETCH_URL, params=params)
            resp.raise_for_status()
            return _parse_abstracts_xml(resp.text, pmids)
        except Exception:
            return {}


def _parse_abstracts_xml(xml_text: str, pmids: list[str]) -> dict[str, str]:
    """Minimal XML parsing for PubMed abstracts without heavy deps."""
    import re

    result: dict[str, str] = {}
    articles = re.split(r"<PubmedArticle>", xml_text)
    for article in articles[1:]:
        pmid_match = re.search(r"<PMID[^>]*>(\d+)</PMID>", article)
        if not pmid_match:
            continue
        pmid = pmid_match.group(1)

        abstract_match = re.search(
            r"<AbstractText[^>]*>(.*?)</AbstractText>", article, re.DOTALL
        )
        if abstract_match:
            text = re.sub(r"<[^>]+>", "", abstract_match.group(1)).strip()
            result[pmid] = text

    return result
