from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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

logger = logging.getLogger("pharm_agent.connectors.ema")

EMA_JSON_URL = (
    "https://www.ema.europa.eu/en/documents/report/"
    "medicines-output-medicines_json-report_en.json"
)

EXPECTED_FIELDS = frozenset({
    "category",
    "name_of_medicine",
    "active_substance",
    "medicine_status",
    "medicine_url",
})

CACHE_FILENAME = "ema_medicines_full.json"
CACHE_MAX_AGE_SECONDS = 12 * 3600  # 12 h (EMA updates twice daily)


@dataclass
class HealthCheckResult:
    http_ok: bool = False
    json_ok: bool = False
    fields_ok: bool = False
    record_count: int = 0
    search_ok: bool = False
    errors: list[str] | None = None

    @property
    def healthy(self) -> bool:
        return self.http_ok and self.json_ok and self.fields_ok and self.record_count > 0


class EMAConnector(BaseConnector):
    """EMA medicines lookup using the public JSON data file.

    Downloads the full medicines JSON, caches it locally, and searches
    by active substance / INN / medicine name in-memory.
    """

    connector_name = "ema"

    def __init__(
        self,
        http_client: httpx.Client | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        super().__init__(http_client)
        self._cache_dir = cache_dir
        self._medicines: list[dict] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _search(self, query: ConnectorQuery) -> ConnectorResult:
        medicines = self._load_medicines()
        if medicines is None:
            return ConnectorResult(
                connector_name=self.connector_name,
                query=query,
                errors=["source_unavailable: EMA data could not be loaded"],
            )

        latin_name = self._pick_latin_name(query)
        terms = self._build_search_terms(latin_name, query)
        matched = self._filter_medicines(medicines, terms, query.max_results)

        sources: list[SourceRecord] = []
        evidence: list[EvidenceItem] = []

        for entry in matched:
            src, evi = self._parse_entry(entry, latin_name)
            sources.append(src)
            evidence.append(evi)

        return ConnectorResult(
            connector_name=self.connector_name,
            query=query,
            sources=sources,
            evidence_items=evidence,
            total_results_available=len(matched),
            results_returned=len(sources),
        )

    def healthcheck(self, test_substance: str = "aspirin") -> HealthCheckResult:
        """Run a multi-step health check against EMA data."""
        result = HealthCheckResult(errors=[])

        # 1. HTTP status check
        try:
            http = self._get_http()
            resp = http.get(EMA_JSON_URL)
            if resp.status_code == 200:
                result.http_ok = True
            else:
                result.errors.append(f"HTTP {resp.status_code}")
                return result
        except Exception as exc:
            result.errors.append(f"HTTP failed: {type(exc).__name__}: {exc}")
            return result

        # 2. JSON parse check
        try:
            data = resp.json()
            result.json_ok = True
        except (json.JSONDecodeError, ValueError) as exc:
            result.errors.append(f"JSON parse failed: {exc}")
            return result

        # 3. Expected fields check
        records = self._extract_records(data)
        if records:
            sample = records[0]
            missing = EXPECTED_FIELDS - set(sample.keys())
            if not missing:
                result.fields_ok = True
            else:
                result.errors.append(f"Missing fields in first record: {missing}")
        else:
            result.errors.append("No records found in JSON")
            return result

        # 4. Record count check
        result.record_count = len(records)
        if result.record_count < 100:
            result.errors.append(
                f"Suspiciously low record count: {result.record_count}"
            )

        # 5. Search test by active substance
        matches = self._filter_medicines(records, [test_substance.lower()], limit=5)
        result.search_ok = len(matches) > 0
        if not result.search_ok:
            result.errors.append(
                f"Search test for '{test_substance}' returned 0 results"
            )

        return result

    # ------------------------------------------------------------------
    # Data loading with cache
    # ------------------------------------------------------------------

    def _load_medicines(self) -> list[dict] | None:
        """Load medicines from cache or network, returning None on total failure."""
        if self._medicines is not None:
            return self._medicines

        cached = self._read_cache()
        if cached is not None and not self._cache_is_stale():
            self._medicines = cached
            return self._medicines

        fetched = self._fetch_from_network()
        if fetched is not None:
            self._medicines = fetched
            self._write_cache(fetched)
            return self._medicines

        if cached is not None:
            logger.warning("Using stale EMA cache (network unavailable)")
            self._medicines = cached
            return self._medicines

        return None

    def _fetch_from_network(self) -> list[dict] | None:
        try:
            http = self._get_http()
            resp = http.get(EMA_JSON_URL)

            if resp.status_code in (429, 503):
                logger.warning("EMA returned %d, falling back to cache", resp.status_code)
                return None
            if resp.status_code != 200:
                logger.warning("EMA HTTP %d", resp.status_code)
                return None

            data = resp.json()
            records = self._extract_records(data)
            if not records:
                logger.warning("EMA JSON has no medicine records")
                return None
            return records
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("EMA fetch failed: %s", exc)
            return None
        except Exception as exc:
            logger.warning("EMA unexpected error: %s", exc)
            return None

    @staticmethod
    def _extract_records(data: object) -> list[dict]:
        """Pull the list of medicine records from whatever wrapper EMA uses."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("data", "content", "medicines"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    return candidate
            return []
        return []

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_path(self) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / CACHE_FILENAME

    def _read_cache(self) -> list[dict] | None:
        path = self._cache_path()
        if path is None or not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, records: list[dict]) -> None:
        path = self._cache_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

    def _cache_is_stale(self) -> bool:
        path = self._cache_path()
        if path is None or not path.exists():
            return True
        age = time.time() - path.stat().st_mtime
        return age > CACHE_MAX_AGE_SECONDS

    # ------------------------------------------------------------------
    # Search helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_search_terms(latin_name: str, query: ConnectorQuery) -> list[str]:
        terms = [latin_name.lower()]
        for syn in query.synonyms:
            low = syn.lower()
            if low not in terms:
                terms.append(low)
        for brand in query.brand_names:
            low = brand.lower()
            if low not in terms:
                terms.append(low)
        return terms

    @staticmethod
    def _filter_medicines(
        records: list[dict], terms: list[str], limit: int = 20,
    ) -> list[dict]:
        matched: list[dict] = []
        for rec in records:
            active = (rec.get("active_substance") or "").lower()
            inn = (rec.get("international_non_proprietary_name_common_name") or "").lower()
            name = (rec.get("name_of_medicine") or "").lower()
            searchable = f"{active} | {inn} | {name}"

            if any(t in searchable for t in terms):
                matched.append(rec)
                if len(matched) >= limit:
                    break
        return matched

    # ------------------------------------------------------------------
    # Result building
    # ------------------------------------------------------------------

    def _parse_entry(
        self, entry: dict, query_term: str,
    ) -> tuple[SourceRecord, EvidenceItem]:
        medicine_name = entry.get("name_of_medicine", "")
        active_substance = entry.get("active_substance", "")
        inn = entry.get("international_non_proprietary_name_common_name", "")
        status = entry.get("medicine_status", "")
        therapeutic_area = entry.get("therapeutic_area_mesh", "")
        atc_code = entry.get("atc_code_human", "")
        url = entry.get("medicine_url", "")
        indication = entry.get("therapeutic_indication", "")
        holder = entry.get("marketing_authorisation_developer_applicant_holder", "")
        ec_date = entry.get("european_commission_decision_date", "")
        product_number = entry.get("ema_product_number", "")

        source_id = f"ema:{product_number}" if product_number else self._make_source_id("ema")
        title = f"EMA: {medicine_name} ({active_substance or inn})"

        source = SourceRecord(
            source_id=source_id,
            source_type=SourceType.ema,
            title=title,
            url_or_path=url or None,
            external_id=product_number or medicine_name,
            publisher="EMA",
            publication_date=ec_date,
            retrieved_at=_now_iso(),
            query_used=query_term,
            raw_payload_hash=_hash_payload(json.dumps(entry, sort_keys=True)),
            citation_label=(
                f"EMA: {medicine_name}. Active substance: {active_substance or inn}. "
                f"Status: {status}. MAH: {holder}."
            ),
            evidence_summary=(
                f"Therapeutic area: {therapeutic_area}. ATC: {atc_code}. "
                f"Indication: {(indication or '')[:200]}."
            ),
            reliability_notes=f"Medicine status: {status}",
        )

        evi = EvidenceItem(
            evidence_id=f"evi:{source_id}",
            source_id=source_id,
            category=EvidenceCategory.regulatory,
            summary=title,
            key_findings=[
                f"Medicine: {medicine_name}",
                f"Active substance: {active_substance or inn}",
                f"Status: {status}",
                f"ATC: {atc_code}",
                f"Holder: {holder}",
            ],
            confidence="high",
        )
        return source, evi
