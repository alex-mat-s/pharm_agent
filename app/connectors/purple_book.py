"""FDA Purple Book connector — biologics and biosimilars.

Purple Book lists licensed biological products with reference product exclusivity
and biosimilar/interchangeable biological product information.

Uses the openFDA /drug/drugsfda.json endpoint filtered for BLA applications.

IMPORTANT: Purple Book should ONLY be used for biologics/biosimilars, not small molecules.
Use molecule_type-based routing:
- biologic / biosimilar / antibody / protein / vaccine → use Purple Book
- small_molecule → skip Purple Book, use NDC / Drugs@FDA / Orange Book

Reference:
- https://www.fda.gov/drugs/biosimilars/purple-book-lists-licensed-biological-products-reference-product-exclusivity
- https://open.fda.gov/apis/drug/drugsfda/
"""

from __future__ import annotations

import json
import logging
from typing import Literal

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

logger = logging.getLogger("pharm_agent.connectors.purple_book")

# Correct endpoint: /drug/drugsfda.json (NOT /drug/nda.json which doesn't exist)
OPENFDA_DRUGSFDA = "https://api.fda.gov/drug/drugsfda.json"

# Molecule types that should use Purple Book
BIOLOGIC_MOLECULE_TYPES = frozenset({
    "biologic",
    "biosimilar",
    "antibody",
    "protein",
    "vaccine",
    "monoclonal_antibody",
    "recombinant_protein",
    "gene_therapy",
    "cell_therapy",
})

# Molecule types that should NOT use Purple Book
SMALL_MOLECULE_TYPES = frozenset({
    "small_molecule",
    "small molecule",
    "chemical",
    "synthetic",
})


def _pick_first(list_val: list[str] | None, default: str = "") -> str:
    if isinstance(list_val, list) and list_val:
        return list_val[0]
    return default


def should_use_purple_book(molecule_type: str | None) -> bool:
    """Determine if Purple Book should be used based on molecule type.
    
    Purple Book is for biologics only, not small molecules.
    
    Args:
        molecule_type: The molecule type (e.g., 'biologic', 'small_molecule', 'antibody')
        
    Returns:
        True if Purple Book should be queried, False otherwise.
    """
    if not molecule_type:
        # Unknown molecule type - skip Purple Book to avoid false positives
        # for drugs like hydroxychloroquine
        return False
    
    normalized = molecule_type.lower().strip().replace("-", "_").replace(" ", "_")
    
    # Explicitly skip small molecules
    if normalized in SMALL_MOLECULE_TYPES:
        return False
    
    # Use Purple Book only for biologics
    return normalized in BIOLOGIC_MOLECULE_TYPES


class PurpleBookConnector(BaseConnector):
    """FDA Purple Book — biologics / biosimilars.

    Uses openFDA Drugs@FDA API filtered for BLA applications.
    
    IMPORTANT: This connector should only be used for biologics.
    Call should_use_purple_book(molecule_type) before searching.
    
    Error handling:
    - 404: no_results (not fatal)
    - 403: source_unavailable (stops iteration)
    """

    connector_name = "purple_book"

    def __init__(self, http_client: httpx.Client | None = None) -> None:
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
        """Search Purple Book for biologics/biosimilars.
        
        Note: This method does NOT check molecule_type. The caller should
        use should_use_purple_book() before calling this connector.
        """
        http = self._get_http()
        search_term = self._pick_latin_name(query)

        # Search openFDA Drugs@FDA for BLA applications
        # Note: Use 'products.active_ingredients.name' (plural), NOT 'products.active_ingredient'
        strategies = [
            f'products.active_ingredients.name:"{search_term}"',
            f'openfda.generic_name:"{search_term}"',
            f'products.brand_name:"{search_term}"',
        ]

        all_results: list[dict] = []
        source_unavailable = False

        for strategy in strategies:
            params = {
                "search": strategy,
                "limit": str(min(query.max_results, 25)),
            }
            try:
                resp = http.get(OPENFDA_DRUGSFDA, params=params, timeout=30.0)
                status_code = resp.status_code
                
                if status_code == 404:
                    # 404 = no results for this query, try next strategy
                    logger.debug(
                        "Purple Book 404 no_results for strategy '%s'",
                        strategy,
                    )
                    continue
                
                if status_code == 403:
                    # 403 = source_unavailable, stop trying
                    source_unavailable = True
                    logger.warning(
                        "Purple Book 403 source_unavailable for strategy '%s'",
                        strategy,
                    )
                    break
                
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                
                # Filter for BLA applications (biologics)
                for item in results:
                    app_no = item.get("application_number", "")
                    # BLA applications start with "BLA"
                    if app_no.upper().startswith("BLA"):
                        all_results.append(item)
                    # Also include NDAs that are marked as biologics
                    elif item.get("application_type", "").lower() in ["bla", "biologics"]:
                        all_results.append(item)
                        
            except httpx.HTTPStatusError as exc:
                logger.warning("Purple Book API error for strategy %s: %s", strategy, exc)
                continue
            except json.JSONDecodeError:
                logger.warning("Purple Book JSON decode error for strategy %s", strategy)
                continue

        # Handle source_unavailable
        if source_unavailable:
            return ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                warnings=[
                    f"source_unavailable: Purple Book returned 403 Forbidden. "
                    f"The FDA API may be temporarily unavailable or rate-limited."
                ],
            )

        seen_apps: set[str] = set()
        sources: list[SourceRecord] = []
        evidence: list[EvidenceItem] = []

        for item in all_results:
            app_no = item.get("application_number", "")
            if not app_no or app_no in seen_apps:
                continue
            seen_apps.add(app_no)

            brand_names: list[str] = item.get("openfda", {}).get("brand_name", []) or []
            generic_names: list[str] = item.get("openfda", {}).get("generic_name", []) or []
            product_name = _pick_first(brand_names, _pick_first(generic_names, "Unknown"))
            generic_name = _pick_first(generic_names, search_term)
            sponsor = item.get("sponsor_name", "")

            # Products (formulations) under this BLA
            products = item.get("products", [])
            product_types = [p.get("product_number", "") for p in products]
            dosage_forms = list({p.get("dosage_form", "") for p in products if p.get("dosage_form")})
            
            # Note: Use 'active_ingredients' (plural) for strength extraction
            strengths = []
            for p in products:
                active_ings = p.get("active_ingredients", [])
                for ai in active_ings:
                    strength = ai.get("strength", "")
                    if strength and strength not in strengths:
                        strengths.append(strength)

            source_id = f"purple_book:{app_no}"
            # Remove BLA prefix for URL if present
            app_no_clean = app_no.replace("BLA", "").replace("NDA", "").strip()
            url = f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo={app_no_clean}"

            sources.append(
                SourceRecord(
                    source_id=source_id,
                    source_type=SourceType.purple_book,
                    title=f"{product_name} ({generic_name})",
                    url_or_path=url,
                    external_id=app_no,
                    publisher=f"FDA / Purple Book (Sponsor: {sponsor})" if sponsor else "FDA / Purple Book",
                    publication_date="",
                    retrieved_at=_now_iso(),
                    query_used=f"active_ingredients.name:{search_term}",
                    raw_payload_hash=_hash_payload(json.dumps(item, sort_keys=True)),
                    citation_label=(
                        f"FDA Purple Book. {product_name} — {generic_name}. "
                        f"Application: {app_no}."
                    ),
                    evidence_summary=(
                        f"FDA biologic/biosimilar: {product_name} ({generic_name}). "
                        f"Sponsor: {sponsor}. Products: {', '.join(product_types)}."
                    ),
                )
            )

            findings: list[str] = []
            if sponsor:
                findings.append(f"Sponsor: {sponsor}")
            if dosage_forms:
                findings.append(f"Dosage forms: {', '.join(dosage_forms)}")
            if strengths:
                findings.append(f"Strengths: {', '.join(strengths[:5])}")
            if product_types:
                findings.append(f"Products: {', '.join(product_types)}")

            # Is this a biosimilar? Check application type
            app_type = item.get("application_type", "")
            is_biosimilar = "biosimilar" in app_type.lower() if app_type else False
            if is_biosimilar:
                findings.append("Type: Biosimilar")
            else:
                findings.append("Type: Reference Biologic")

            evidence.append(
                EvidenceItem(
                    evidence_id=f"evi:{source_id}",
                    source_id=source_id,
                    category=EvidenceCategory.regulatory,
                    summary=f"FDA biologic via Purple Book: {product_name} ({generic_name})",
                    key_findings=findings,
                    confidence="high",
                )
            )

            if len(sources) >= query.max_results:
                break

        warnings: list[str] = []
        if not sources:
            warnings.append(
                f"no_results: No Purple Book results for '{search_term}'. "
                "This molecule may not be a biologic or may not be in the Purple Book database. "
                "Note: Purple Book is for biologics only. For small molecules, use Orange Book or NDC."
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
