"""EPO Open Patent Services (OPS) connector.

Free registration required at https://developers.epo.org/ for OAuth credentials.
Docs: https://documents.epo.org/projects/babylon/eponet.nsf/0/
        F3B3CCCFD52594B2C125836A0040B67C/$File/ops_v3.2_documentation_en.pdf

Without credentials the connector returns gracefully with a warning.
"""

from __future__ import annotations

import base64
import json
import logging
import xml.etree.ElementTree as ET
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

logger = logging.getLogger("pharm_agent.connectors")

EPO_OPS_SEARCH = "https://ops.epo.org/3.2/rest-services/published-data/search"
EPO_AUTH = "https://ops.epo.org/3.2/auth/accesstoken"

# OPS namespaces
_NS_OPS = {"ops": "http://ops.epo.org", "epo": "http://www.epo.org/exchange"}


def _get_text(elem: ET.Element | None, tag: str, ns: dict[str, str] | None = None) -> str:
    """Safely extract text from an XML child element."""
    if elem is None:
        return ""
    child = elem.find(tag, ns or _NS_OPS)
    return (child.text or "").strip() if child is not None and child.text else ""


class EPOOPSConnector(BaseConnector):
    """European Patent Office — OPS patent search.

    OAuth 2.0 client-credentials flow.  Rate limit: 4 req/min for free tier.
    """

    connector_name = "epo_ops"

    def __init__(
        self,
        http_client: httpx.Client | None = None,
        consumer_key: str | None = None,
        consumer_secret: str | None = None,
    ) -> None:
        super().__init__(http_client)
        self._consumer_key = consumer_key
        self._consumer_secret = consumer_secret
        self._access_token: str | None = None
        self._token_expires: datetime | None = None

    def _has_credentials(self) -> bool:
        return bool(self._consumer_key and self._consumer_secret)

    def _get_access_token(self, http: httpx.Client) -> str | None:
        """Obtain (or refresh) an OAuth 2.0 access token."""
        if not self._has_credentials():
            return None

        if self._access_token and self._token_expires and datetime.now(UTC) < self._token_expires:
            return self._access_token

        try:
            credentials = base64.b64encode(
                f"{self._consumer_key}:{self._consumer_secret}".encode()
            ).decode()

            resp = http.post(
                EPO_AUTH,
                headers={"Authorization": f"Basic {credentials}"},
                data={"grant_type": "client_credentials"},
                timeout=30.0,
            )
            resp.raise_for_status()
            token_data = resp.json()
            self._access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 1200)
            self._token_expires = datetime.fromtimestamp(
                datetime.now(UTC).timestamp() + expires_in - 60, tz=UTC
            )
            return self._access_token
        except Exception as exc:
            logger.warning("EPO OPS token acquisition failed: %s", exc)
            return None

    def _search(self, query: ConnectorQuery) -> ConnectorResult:
        http = self._get_http()
        search_term = self._pick_latin_name(query)

        if not self._has_credentials():
            return ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                warnings=[
                    "EPO OPS credentials not configured. "
                    "Set EPO_OPS_CONSUMER_KEY and EPO_OPS_CONSUMER_SECRET env vars. "
                    "Register free at https://developers.epo.org/"
                ],
            )

        token = self._get_access_token(http)
        if not token:
            return ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                warnings=["EPO OPS token could not be acquired."],
            )

        # Build CQL query for title/abstract
        cql = f'ta all "{search_term}"'

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/exchange+xml",
        }

        try:
            resp = http.get(
                EPO_OPS_SEARCH,
                headers=headers,
                params={"q": cql, "Range": f"1-{min(query.max_results, 25)}"},
                timeout=30.0,
            )
            if resp.status_code == 404:
                return ConnectorResult(
                    connector_name=self.connector_name,
                    query=query,
                    warnings=[f"No EPO OPS results for '{search_term}'"],
                )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("EPO OPS API error: %s", exc)
            return ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                errors=[f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"],
            )
        except Exception as exc:
            logger.warning("EPO OPS request failed: %s", exc)
            return ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                errors=[f"{type(exc).__name__}: {exc}"],
            )

        # Parse XML
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            return ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                errors=[f"XML parse error: {exc}"],
            )

        sources: list[SourceRecord] = []
        evidence: list[EvidenceItem] = []

        # OPS response structure: exchange-documents/exchange-document
        for doc in root.findall(".//epo:exchange-document", _NS_OPS):
            doc_number = doc.get("doc-number", "")
            country = doc.get("country", "")
            kind = doc.get("kind", "")
            family_id = doc.get("family-id", "")

            if not doc_number:
                continue

            # Extract title
            title_elem = doc.find(".//epo:invention-title", _NS_OPS)
            title = _get_text(title_elem, ".")
            if not isinstance(title, str) or not title.strip():
                title = f"EP Patent {country}{doc_number}"

            # Publication date
            date_elem = doc.find(".//epo:publication-reference/epo:document-id/epo:date", _NS_OPS)
            pub_date = _get_text(date_elem, ".") if date_elem is not None else ""
            # Format: YYYYMMDD → YYYY-MM-DD
            if pub_date and len(pub_date) == 8:
                pub_date = f"{pub_date[:4]}-{pub_date[4:6]}-{pub_date[6:8]}"

            # Applicants (assignees)
            applicants = doc.findall(".//epo:applicant/epo:name", _NS_OPS)
            applicant_names = []
            for a in applicants:
                if a.text:
                    applicant_names.append(a.text.strip())
            applicant_str = ", ".join(applicant_names) if applicant_names else "Unknown"

            patent_id = f"{country}{doc_number}"
            source_id = f"epo_ops:{patent_id}"
            url = f"https://register.epo.org/application?number={patent_id}"

            sources.append(
                SourceRecord(
                    source_id=source_id,
                    source_type=SourceType.epo_ops,
                    title=title,
                    url_or_path=url,
                    external_id=patent_id,
                    publisher=f"EPO (Applicant: {applicant_str})" if applicant_str != "Unknown" else "EPO",
                    publication_date=pub_date,
                    retrieved_at=_now_iso(),
                    query_used=cql,
                    raw_payload_hash=_hash_payload(ET.tostring(doc, encoding="unicode")),
                    citation_label=(
                        f"EPO Patent {patent_id}. {title}. "
                        f"Applicant: {applicant_str}."
                    ),
                    evidence_summary=(
                        f"European patent {patent_id}: {title}. "
                        f"Kind: {kind}. Family ID: {family_id}. "
                        f"Applicant: {applicant_str}."
                    ),
                )
            )

            findings: list[str] = []
            if kind:
                findings.append(f"Kind: {kind}")
            if family_id:
                findings.append(f"Family ID: {family_id}")
            if applicant_str != "Unknown":
                findings.append(f"Applicant: {applicant_str}")

            evidence.append(
                EvidenceItem(
                    evidence_id=f"evi:{source_id}",
                    source_id=source_id,
                    category=EvidenceCategory.patent,
                    summary=f"European patent {patent_id}: {title}",
                    key_findings=findings,
                    confidence="medium",
                )
            )

        warnings: list[str] = []
        if not sources:
            warnings.append(
                f"No EPO OPS patents found for '{search_term}'. "
                "This does not mean no patents exist."
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
