"""Patent Aggregator — orchestrates patent search across RU/EA and international sources.

Implements fallback logic per .clinerules/07-ru-eapo-patent-workflow.md:
1. Rospatent Open API / Open Data
2. Local Rospatent cache
3. FIPS search or registers
4. EAPO registry / bulletin
5. EPO OPS (if credentials configured)
6. WIPO / Google Patents (discovery fallback)
7. Return warnings + require manual review

No source is fatal. All failures are logged, warnings returned, manual review flagged.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

from app.cache.patent_cache import PatentCache
from app.config import config
from app.connectors.eapo.eapo_registry import EAPORegistryConnector
from app.connectors.epo_ops import EPOOPSConnector
from app.connectors.ru_patent.fips_registers import FIPSRegistersConnector
from app.connectors.ru_patent.fips_search import FIPSSearchConnector
from app.connectors.ru_patent.rospatent import RospatentConnector
from app.connectors.uspto import USPTOConnector
from app.connectors.wipo import WIPOConnector
from app.logging.audit_logger import log_tool_call
from app.schemas.evidence import ConnectorQuery
from app.schemas.ru_patent import (
    AggregatedPatentResult,
    BlockingRisk,
    LegalStatus,
    PatentEvidence,
    PatentFamilyEvidence,
    PatentQuery,
    PatentSearchResult,
    PATENT_DISCLAIMER_EN,
    PATENT_DISCLAIMER_RU,
)

if TYPE_CHECKING:
    from app.connectors.ru_patent.base_ru_connector import BaseRuPatentConnector

logger = logging.getLogger("pharm_agent.connectors.patent_aggregator")


def _now_iso() -> str:
    """Return current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).isoformat()


class PatentAggregator:
    """Orchestrates patent search across RU/EA and international sources.

    Implements fallback logic:
    1. Rospatent Open API / Open Data
    2. Local Rospatent cache
    3. FIPS search or registers
    4. EAPO registry / bulletin
    5. EPO OPS (if credentials configured)
    6. WIPO (discovery fallback)
    7. USPTO (US patents as reference)

    All sources are non-fatal. The aggregator:
    - Logs all attempts
    - Returns warnings for unavailable sources
    - Flags when manual review is required
    - Clusters results by patent family
    - Verifies legal status where possible
    """

    def __init__(
        self,
        http_client: httpx.Client | None = None,
        use_cache: bool | None = None,
    ) -> None:
        """Initialize the patent aggregator.

        Args:
            http_client: Optional HTTP client for testing.
            use_cache: Override config.patent_cache_enabled.
        """
        self._http = http_client
        self._use_cache = use_cache if use_cache is not None else config.patent_cache_enabled

        # Initialize connectors lazily
        self._rospatent: RospatentConnector | None = None
        self._fips_search: FIPSSearchConnector | None = None
        self._fips_registers: FIPSRegistersConnector | None = None
        self._eapo: EAPORegistryConnector | None = None
        self._epo_ops: EPOOPSConnector | None = None
        self._wipo: WIPOConnector | None = None
        self._uspto: USPTOConnector | None = None

    def _get_rospatent(self) -> RospatentConnector:
        """Get or create Rospatent connector."""
        if self._rospatent is None:
            self._rospatent = RospatentConnector(
                http_client=self._http,
                use_cache=self._use_cache,
            )
        return self._rospatent

    def _get_fips_search(self) -> FIPSSearchConnector:
        """Get or create FIPS Search connector."""
        if self._fips_search is None:
            self._fips_search = FIPSSearchConnector(
                http_client=self._http,
                use_cache=self._use_cache,
            )
        return self._fips_search

    def _get_fips_registers(self) -> FIPSRegistersConnector:
        """Get or create FIPS Registers connector."""
        if self._fips_registers is None:
            self._fips_registers = FIPSRegistersConnector(
                http_client=self._http,
                use_cache=self._use_cache,
            )
        return self._fips_registers

    def _get_eapo(self) -> EAPORegistryConnector:
        """Get or create EAPO connector."""
        if self._eapo is None:
            self._eapo = EAPORegistryConnector(
                http_client=self._http,
                use_cache=self._use_cache,
            )
        return self._eapo

    def _get_epo_ops(self) -> EPOOPSConnector:
        """Get or create EPO OPS connector."""
        if self._epo_ops is None:
            self._epo_ops = EPOOPSConnector(
                http_client=self._http,
                consumer_key=config.epo_ops_consumer_key,
                consumer_secret=config.epo_ops_consumer_secret,
            )
        return self._epo_ops

    def _get_wipo(self) -> WIPOConnector:
        """Get or create WIPO connector."""
        if self._wipo is None:
            self._wipo = WIPOConnector(http_client=self._http)
        return self._wipo

    def _get_uspto(self) -> USPTOConnector:
        """Get or create USPTO connector."""
        if self._uspto is None:
            self._uspto = USPTOConnector(http_client=self._http)
        return self._uspto

    def search_all_sources(
        self,
        query: PatentQuery,
        *,
        run_id: str = "unknown",
        include_international: bool = True,
        include_us: bool = False,
    ) -> AggregatedPatentResult:
        """Search all available patent sources with fallback logic.

        Args:
            query: Patent search query.
            run_id: Run ID for audit logging.
            include_international: Include EPO OPS and WIPO.
            include_us: Include USPTO (US patents).

        Returns:
            Aggregated result with patents from all sources and diagnostics.
        """
        start = time.monotonic()
        result = AggregatedPatentResult(query=query)

        # Step 1: Try Rospatent
        rospatent_ok = self._try_rospatent(query, run_id, result)

        # Step 2: Try FIPS Search (always, for additional coverage)
        self._try_fips_search(query, run_id, result)

        # Step 3: Try EAPO Registry
        self._try_eapo(query, run_id, result)

        # Step 4: International fallbacks
        if include_international:
            self._try_epo_ops(query, run_id, result)
            self._try_wipo(query, run_id, result)

        # Step 5: US patents (optional)
        if include_us:
            self._try_uspto(query, run_id, result)

        # Step 6: Verify legal status for RU/EA patents
        self._verify_legal_status_all(result, run_id)

        # Step 7: Cluster by family
        result.patent_families = self.cluster_by_family(result.all_patents)

        # Step 8: Check if manual review required
        self._assess_manual_review(result)

        # Log aggregated search
        elapsed = int((time.monotonic() - start) * 1000)
        log_tool_call(
            run_id=run_id,
            stage="patent_analysis",
            tool_name="patent_aggregator",
            status="succeeded",
            duration_ms=elapsed,
            input_summary={
                "query": query.inn,
                "indication": query.indication,
                "include_international": include_international,
            },
            output_summary={
                "total_patents": len(result.all_patents),
                "families": len(result.patent_families),
                "sources_available": len(result.sources_available),
                "sources_unavailable": len(result.sources_unavailable),
                "requires_manual_review": result.requires_manual_review,
            },
        )

        return result

    def _try_rospatent(
        self,
        query: PatentQuery,
        run_id: str,
        result: AggregatedPatentResult,
    ) -> bool:
        """Try Rospatent search. Returns True if successful."""
        result.sources_queried.append("rospatent")

        try:
            connector = self._get_rospatent()
            search_result = connector.search_patents(query, run_id=run_id)
            result.rospatent_results = search_result

            if search_result.source_available and not search_result.errors:
                result.sources_available.append("rospatent")
                result.all_patents.extend(search_result.patents)
                result.total_warnings.extend(search_result.warnings)
                return True
            else:
                result.sources_unavailable.append("rospatent")
                result.total_warnings.extend(search_result.warnings)
                result.total_warnings.extend(search_result.errors)
                return False

        except Exception as e:
            logger.warning("Rospatent search failed: %s", e)
            result.sources_unavailable.append("rospatent")
            result.total_warnings.append(f"Rospatent search error: {e}")
            return False

    def _try_fips_search(
        self,
        query: PatentQuery,
        run_id: str,
        result: AggregatedPatentResult,
    ) -> bool:
        """Try FIPS search."""
        result.sources_queried.append("fips")

        try:
            connector = self._get_fips_search()
            search_result = connector.search_patents(query, run_id=run_id)
            result.fips_results = search_result

            if search_result.source_available:
                result.sources_available.append("fips")
                result.all_patents.extend(search_result.patents)
                result.total_warnings.extend(search_result.warnings)
                return True
            else:
                result.sources_unavailable.append("fips")
                result.total_warnings.extend(search_result.warnings)
                return False

        except Exception as e:
            logger.warning("FIPS search failed: %s", e)
            result.sources_unavailable.append("fips")
            result.total_warnings.append(f"FIPS search error: {e}")
            return False

    def _try_eapo(
        self,
        query: PatentQuery,
        run_id: str,
        result: AggregatedPatentResult,
    ) -> bool:
        """Try EAPO registry search."""
        result.sources_queried.append("eapo")

        try:
            connector = self._get_eapo()
            search_result = connector.search_patents(query, run_id=run_id)
            result.eapo_results = search_result

            if search_result.source_available:
                result.sources_available.append("eapo")
                result.all_patents.extend(search_result.patents)
                result.total_warnings.extend(search_result.warnings)
                return True
            else:
                result.sources_unavailable.append("eapo")
                result.total_warnings.extend(search_result.warnings)
                return False

        except Exception as e:
            logger.warning("EAPO search failed: %s", e)
            result.sources_unavailable.append("eapo")
            result.total_warnings.append(f"EAPO search error: {e}")
            return False

    def _try_epo_ops(
        self,
        query: PatentQuery,
        run_id: str,
        result: AggregatedPatentResult,
    ) -> bool:
        """Try EPO OPS search."""
        result.sources_queried.append("epo_ops")

        # Check if credentials are configured
        if not config.epo_ops_consumer_key or not config.epo_ops_consumer_secret:
            result.total_warnings.append(
                "EPO OPS credentials not configured. "
                "Set EPO_OPS_CONSUMER_KEY and EPO_OPS_CONSUMER_SECRET for EPO patent search."
            )
            result.sources_unavailable.append("epo_ops")
            return False

        try:
            connector = self._get_epo_ops()

            # Convert PatentQuery to ConnectorQuery
            connector_query = ConnectorQuery(
                inn=query.inn,
                disease=query.indication,
                synonyms=query.inn_synonyms,
                brand_names=query.brand_names,
                max_results=query.max_results,
            )

            search_result = connector.search(connector_query, run_id=run_id)

            # Convert SourceRecords to PatentEvidence
            for source in search_result.sources:
                patent = PatentEvidence(
                    source_id=source.source_id,
                    source_type="epo_ops",
                    jurisdiction="EP",
                    document_number=source.external_id or source.source_id.split(":")[-1],
                    title=source.title,
                    publication_date=source.publication_date,
                    source_url=source.url_or_path,
                    retrieved_at=source.retrieved_at,
                    warnings=[],
                )
                result.all_patents.append(patent)

            if search_result.sources:
                result.sources_available.append("epo_ops")
                result.epo_results = PatentSearchResult(
                    connector_name="epo_ops",
                    query=query,
                    patents=[],  # Already added to all_patents
                    results_returned=len(search_result.sources),
                    source_available=True,
                    warnings=search_result.warnings,
                )
                result.total_warnings.extend(search_result.warnings)
                return True
            else:
                result.sources_unavailable.append("epo_ops")
                result.total_warnings.extend(search_result.warnings)
                return False

        except Exception as e:
            logger.warning("EPO OPS search failed: %s", e)
            result.sources_unavailable.append("epo_ops")
            result.total_warnings.append(f"EPO OPS search error: {e}")
            return False

    def _try_wipo(
        self,
        query: PatentQuery,
        run_id: str,
        result: AggregatedPatentResult,
    ) -> bool:
        """Try WIPO search."""
        result.sources_queried.append("wipo")

        try:
            connector = self._get_wipo()

            connector_query = ConnectorQuery(
                inn=query.inn,
                disease=query.indication,
                synonyms=query.inn_synonyms,
                brand_names=query.brand_names,
                max_results=query.max_results,
            )

            search_result = connector.search(connector_query, run_id=run_id)

            # WIPO connector returns warnings about no API
            result.total_warnings.extend(search_result.warnings)

            if search_result.sources:
                result.sources_available.append("wipo")
                return True
            else:
                # WIPO has no REST API, so this is expected
                result.sources_unavailable.append("wipo")
                return False

        except Exception as e:
            logger.warning("WIPO search failed: %s", e)
            result.sources_unavailable.append("wipo")
            result.total_warnings.append(f"WIPO search error: {e}")
            return False

    def _try_uspto(
        self,
        query: PatentQuery,
        run_id: str,
        result: AggregatedPatentResult,
    ) -> bool:
        """Try USPTO search."""
        result.sources_queried.append("uspto")

        try:
            connector = self._get_uspto()

            connector_query = ConnectorQuery(
                inn=query.inn,
                disease=query.indication,
                synonyms=query.inn_synonyms,
                brand_names=query.brand_names,
                max_results=query.max_results,
            )

            search_result = connector.search(connector_query, run_id=run_id)

            # Convert SourceRecords to PatentEvidence
            for source in search_result.sources:
                patent = PatentEvidence(
                    source_id=source.source_id,
                    source_type="uspto",
                    jurisdiction="US",
                    document_number=source.external_id or source.source_id.split(":")[-1],
                    title=source.title,
                    publication_date=source.publication_date,
                    source_url=source.url_or_path,
                    retrieved_at=source.retrieved_at,
                    warnings=[],
                )
                result.all_patents.append(patent)

            if search_result.sources:
                result.sources_available.append("uspto")
                result.uspto_results = PatentSearchResult(
                    connector_name="uspto",
                    query=query,
                    patents=[],
                    results_returned=len(search_result.sources),
                    source_available=True,
                    warnings=search_result.warnings,
                )
                result.total_warnings.extend(search_result.warnings)
                return True
            else:
                result.sources_unavailable.append("uspto")
                result.total_warnings.extend(search_result.warnings)
                return False

        except Exception as e:
            logger.warning("USPTO search failed: %s", e)
            result.sources_unavailable.append("uspto")
            result.total_warnings.append(f"USPTO search error: {e}")
            return False

    def _verify_legal_status_all(
        self,
        result: AggregatedPatentResult,
        run_id: str,
    ) -> None:
        """Verify legal status for all RU and EA patents."""
        fips_registers = self._get_fips_registers()

        for patent in result.all_patents:
            if patent.legal_status != LegalStatus.unknown:
                continue  # Already has status

            if patent.jurisdiction == "RU":
                status, warnings = fips_registers.get_legal_status(
                    patent.document_number,
                    run_id=run_id,
                )
                patent.legal_status = status
                patent.warnings.extend(warnings)

            elif patent.jurisdiction == "EA":
                eapo = self._get_eapo()
                status, warnings = eapo.get_legal_status(
                    patent.document_number,
                    run_id=run_id,
                )
                patent.legal_status = status
                patent.warnings.extend(warnings)

    def cluster_by_family(
        self,
        patents: list[PatentEvidence],
    ) -> list[PatentFamilyEvidence]:
        """Deduplicate and cluster patents by family.

        Clustering is based on:
        - Priority number (if available)
        - Application number similarity
        - Title similarity
        - Applicant similarity
        """
        if not patents:
            return []

        families: list[PatentFamilyEvidence] = []
        processed: set[str] = set()

        for patent in patents:
            if patent.source_id in processed:
                continue

            # Find related patents
            related = [patent]
            for other in patents:
                if other.source_id in processed:
                    continue
                if other.source_id == patent.source_id:
                    continue

                if self._are_related(patent, other):
                    related.append(other)

            # Mark as processed
            for p in related:
                processed.add(p.source_id)

            # Create family
            family = self._create_family(related)
            families.append(family)

        return families

    def _are_related(
        self,
        patent1: PatentEvidence,
        patent2: PatentEvidence,
    ) -> bool:
        """Check if two patents are likely from the same family."""
        # Same priority date and applicant
        if (
            patent1.priority_date
            and patent1.priority_date == patent2.priority_date
            and patent1.applicants
            and patent2.applicants
            and patent1.applicants[0].lower() == patent2.applicants[0].lower()
        ):
            return True

        # Similar title (simple check)
        if patent1.title and patent2.title:
            title1_words = set(patent1.title.lower().split())
            title2_words = set(patent2.title.lower().split())
            if len(title1_words & title2_words) > len(title1_words) * 0.7:
                return True

        return False

    def _create_family(
        self,
        patents: list[PatentEvidence],
    ) -> PatentFamilyEvidence:
        """Create a PatentFamilyEvidence from related patents."""
        # Generate family ID
        family_id = self._generate_family_id(patents)

        # Collect jurisdictions
        jurisdictions = list({p.jurisdiction for p in patents})

        # Find earliest priority date
        priority_dates = [p.priority_date for p in patents if p.priority_date]
        earliest_priority = min(priority_dates) if priority_dates else None

        # Collect applicants
        all_applicants: list[str] = []
        for p in patents:
            all_applicants.extend(p.applicants)
        main_applicants = list(dict.fromkeys(all_applicants))[:5]

        # Determine highest blocking risk
        risks = [p.blocking_risk_preliminary for p in patents]
        highest_risk = BlockingRisk.unknown
        for risk in [BlockingRisk.high, BlockingRisk.medium, BlockingRisk.low]:
            if risk in risks:
                highest_risk = risk
                break

        # Blocking jurisdictions
        blocking_jurisdictions = [
            p.jurisdiction
            for p in patents
            if p.blocking_risk_preliminary in [BlockingRisk.high, BlockingRisk.medium]
        ]

        # Collect patent types
        all_types = []
        for p in patents:
            all_types.extend(p.patent_types)
        patent_types = list(dict.fromkeys(all_types))

        return PatentFamilyEvidence(
            family_id=family_id,
            priority_number=patents[0].priority_date,
            members=patents,
            jurisdictions=jurisdictions,
            earliest_priority_date=earliest_priority,
            main_applicants=main_applicants,
            highest_blocking_risk=highest_risk,
            blocking_jurisdictions=blocking_jurisdictions,
            patent_types=patent_types,
        )

    def _generate_family_id(self, patents: list[PatentEvidence]) -> str:
        """Generate a unique family ID from patents."""
        # Use hash of sorted source IDs
        ids = sorted(p.source_id for p in patents)
        combined = ":".join(ids)
        return f"family:{hashlib.sha256(combined.encode()).hexdigest()[:12]}"

    def _assess_manual_review(self, result: AggregatedPatentResult) -> None:
        """Assess if manual review is required."""
        reasons: list[str] = []

        # No sources available
        if not result.sources_available:
            result.requires_manual_review = True
            reasons.append(
                "No patent sources were available. Manual patent search required."
            )

        # No patents found
        elif not result.all_patents:
            result.requires_manual_review = True
            reasons.append(
                "No patents found. This does not mean no patents exist. "
                "Manual verification required."
            )

        # Primary RU sources unavailable
        elif "rospatent" not in result.sources_available and "fips" not in result.sources_available:
            result.requires_manual_review = True
            reasons.append(
                "Primary Russian patent sources (Rospatent, FIPS) were unavailable. "
                "Manual verification of Russian patents recommended."
            )

        # Patents with unknown status
        unknown_status_count = sum(
            1 for p in result.all_patents
            if p.jurisdiction in ["RU", "EA"] and p.legal_status == LegalStatus.unknown
        )
        if unknown_status_count > 0:
            result.requires_manual_review = True
            reasons.append(
                f"{unknown_status_count} RU/EA patents have unknown legal status. "
                "Manual verification recommended."
            )

        result.manual_review_reasons = reasons

    def get_disclaimers(self) -> tuple[str, str]:
        """Return patent analysis disclaimers (English, Russian)."""
        return PATENT_DISCLAIMER_EN, PATENT_DISCLAIMER_RU
