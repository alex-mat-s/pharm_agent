# Russian and Eurasian Patent Workflow — Implementation Plan

## Document Purpose

This document outlines the technical implementation plan for adding Russian (Rospatent/FIPS) and Eurasian (EAPO) patent-analysis capabilities to the pharm_agent project.

**Status:** Awaiting approval before implementation.

---

## 1. Proposed Folder Structure

```text
app/
├── connectors/
│   ├── ru_patent/                      # New: Russian patent connectors
│   │   ├── __init__.py
│   │   ├── rospatent.py               # Rospatent Open Data / Open API
│   │   ├── fips_search.py             # FIPS information search
│   │   ├── fips_registers.py          # FIPS Open Registers (legal status)
│   │   └── base_ru_connector.py       # Shared RU patent utilities
│   ├── eapo/                          # New: Eurasian patent connectors
│   │   ├── __init__.py
│   │   ├── eapo_registry.py           # EAPO Patent Registry
│   │   ├── eapo_bulletin.py           # EAPO Bulletin (optional)
│   │   └── eapatis.py                 # EAPATIS (if accessible)
│   ├── patent_aggregator.py           # New: Orchestrates RU/EA + international fallbacks
│   └── ... (existing connectors)
├── schemas/
│   ├── ru_patent.py                   # New: PatentEvidence, PatentFamilyEvidence
│   └── ... (existing schemas)
├── cache/                             # New: Local patent cache layer
│   ├── __init__.py
│   ├── patent_cache.py                # Cache manager
│   └── cache_schemas.py               # Cache entry schemas
data/
├── cache/
│   └── patents/
│       ├── ru/                        # Russian patent cache
│       ├── eapo/                      # Eurasian patent cache
│       └── international/             # EPO/WIPO/USPTO cache
tests/
├── connectors/
│   ├── ru_patent/                     # New: RU patent connector tests
│   │   ├── __init__.py
│   │   ├── test_rospatent.py
│   │   ├── test_fips_search.py
│   │   ├── test_fips_registers.py
│   │   └── fixtures/                  # Mock responses
│   │       ├── rospatent_response.json
│   │       └── fips_response.json
│   ├── eapo/                          # New: EAPO connector tests
│   │   ├── __init__.py
│   │   ├── test_eapo_registry.py
│   │   └── fixtures/
│   │       └── eapo_registry_response.json
│   └── test_patent_aggregator.py      # New: Aggregator tests
```

---

## 2. Connector Interfaces

### 2.1 Base RU/EA Patent Connector

```python
# app/connectors/ru_patent/base_ru_connector.py

from abc import ABC, abstractmethod
from app.connectors.base import BaseConnector
from app.schemas.ru_patent import PatentEvidence, PatentQuery, PatentSearchResult

class BaseRuPatentConnector(BaseConnector, ABC):
    """Base class for Russian/Eurasian patent connectors.
    
    Extends BaseConnector with patent-specific methods:
    - Query expansion (INN → synonyms, brands, targets)
    - Legal status lookup
    - Patent evidence normalization
    """
    
    @abstractmethod
    def search_patents(
        self, 
        query: PatentQuery, 
        *, 
        run_id: str = "unknown"
    ) -> PatentSearchResult:
        """Search for patents matching the query."""
        ...
    
    @abstractmethod
    def get_legal_status(
        self, 
        document_number: str, 
        *, 
        run_id: str = "unknown"
    ) -> PatentEvidence | None:
        """Fetch legal status for a specific document."""
        ...
    
    def expand_query(self, query: PatentQuery) -> list[str]:
        """Expand query into multiple search terms."""
        terms = [query.inn]
        terms.extend(query.inn_synonyms)
        terms.extend(query.brand_names)
        if query.molecular_target:
            terms.append(query.molecular_target)
        if query.indication:
            terms.append(query.indication)
        return [t for t in terms if t]
```

### 2.2 Rospatent Connector Interface

```python
# app/connectors/ru_patent/rospatent.py

class RospatentConnector(BaseRuPatentConnector):
    """Rospatent Open Data / Open API connector.
    
    Primary source for Russian patent discovery.
    Supports:
    - Patent search by text query
    - Patent retrieval by number
    - Open data bulk files (cached)
    
    Environment:
    - ROSPATENT_BASE_URL (default: https://online.rospatent.gov.ru)
    - ROSPATENT_API_KEY (optional, for extended access)
    """
    
    connector_name = "rospatent"
    
    def search_patents(self, query: PatentQuery, *, run_id: str = "unknown") -> PatentSearchResult:
        """Search Rospatent Open API or cached open data."""
        ...
    
    def get_legal_status(self, document_number: str, *, run_id: str = "unknown") -> PatentEvidence | None:
        """Fetch legal status from Rospatent."""
        ...
    
    def _search_open_api(self, terms: list[str]) -> list[PatentEvidence]:
        """Try Rospatent Open API."""
        ...
    
    def _search_cached_data(self, terms: list[str]) -> list[PatentEvidence]:
        """Search local Rospatent open data cache."""
        ...
```

### 2.3 FIPS Connectors

```python
# app/connectors/ru_patent/fips_search.py

class FIPSSearchConnector(BaseRuPatentConnector):
    """FIPS information search system connector.
    
    Secondary source for Russian patent discovery.
    """
    connector_name = "fips_search"

# app/connectors/ru_patent/fips_registers.py

class FIPSRegistersConnector(BaseRuPatentConnector):
    """FIPS Open Registers connector.
    
    Primary source for legal status verification.
    Used for document-by-number lookup.
    """
    connector_name = "fips_registers"
    
    def verify_legal_status(
        self, 
        patent_number: str, 
        *, 
        run_id: str = "unknown"
    ) -> tuple[str, list[str]]:
        """Verify legal status. Returns (status, warnings)."""
        ...
```

### 2.4 EAPO Connector

```python
# app/connectors/eapo/eapo_registry.py

class EAPORegistryConnector(BaseRuPatentConnector):
    """EAPO Patent Registry connector.
    
    Eurasian patents that may be relevant to Russia and the Eurasian region.
    """
    connector_name = "eapo_registry"
    
    def search_patents(self, query: PatentQuery, *, run_id: str = "unknown") -> PatentSearchResult:
        """Search EAPO Registry."""
        ...
    
    def get_legal_status(self, ea_number: str, *, run_id: str = "unknown") -> PatentEvidence | None:
        """Fetch maintenance/validity status from EAPO."""
        ...
```

### 2.5 Patent Aggregator

```python
# app/connectors/patent_aggregator.py

class PatentAggregator:
    """Orchestrates patent search across RU/EA and international sources.
    
    Implements fallback logic from .clinerules/07-ru-eapo-patent-workflow.md:
    1. Rospatent Open API / Open Data
    2. Local Rospatent cache
    3. FIPS search or registers
    4. EAPO registry / bulletin
    5. EPO OPS (if credentials configured)
    6. WIPO / Google Patents (discovery fallback)
    7. Return warnings + require manual review
    """
    
    def search_all_sources(
        self, 
        query: PatentQuery, 
        *, 
        run_id: str,
        include_international: bool = True,
    ) -> AggregatedPatentResult:
        """Search all available sources with fallback logic."""
        ...
    
    def cluster_by_family(
        self, 
        patents: list[PatentEvidence]
    ) -> list[PatentFamilyEvidence]:
        """Deduplicate and cluster results by patent family."""
        ...
    
    def verify_all_legal_status(
        self, 
        patents: list[PatentEvidence], 
        *, 
        run_id: str
    ) -> list[PatentEvidence]:
        """Verify legal status for all patents."""
        ...
```

---

## 3. Environment Variables

Add to `app/config.py`:

```python
class Config(BaseSettings):
    # ... existing fields ...
    
    # Russian Patent Sources
    rospatent_base_url: str = "https://online.rospatent.gov.ru"
    rospatent_api_key: str | None = None
    fips_base_url: str = "https://www.fips.ru"
    fips_registers_base_url: str = "https://www.fips.ru/registers-web"
    
    # EAPO Sources
    eapo_base_url: str = "https://www.eapo.org"
    eapo_registry_url: str = "https://www.eapo.org/pubservices/info/registry/inventions/patents"
    
    # International Fallbacks (existing + new)
    # epo_ops_consumer_key: str | None = None  # already exists
    # epo_ops_consumer_secret: str | None = None  # already exists
    wipo_patentscope_base_url: str = "https://patentscope.wipo.int"
    google_patents_base_url: str = "https://patents.google.com"
    
    # Patent Cache Configuration
    patent_cache_dir: Path = Path("./data/cache/patents")
    russian_patent_cache_dir: Path = Path("./data/cache/patents/ru")
    eapo_patent_cache_dir: Path = Path("./data/cache/patents/eapo")
    patent_cache_ttl_days: int = 7  # Cache TTL for legal status
    patent_cache_enabled: bool = True
```

Add to `.env.example`:

```bash
# Russian Patent Sources
ROSPATENT_BASE_URL=https://online.rospatent.gov.ru
ROSPATENT_API_KEY=
FIPS_BASE_URL=https://www.fips.ru
FIPS_REGISTERS_BASE_URL=https://www.fips.ru/registers-web

# EAPO Sources
EAPO_BASE_URL=https://www.eapo.org
EAPO_REGISTRY_URL=https://www.eapo.org/pubservices/info/registry/inventions/patents

# International Fallbacks
WIPO_PATENTSCOPE_BASE_URL=https://patentscope.wipo.int
GOOGLE_PATENTS_BASE_URL=https://patents.google.com

# Patent Cache
PATENT_CACHE_DIR=./data/cache/patents
RUSSIAN_PATENT_CACHE_DIR=./data/cache/patents/ru
EAPO_PATENT_CACHE_DIR=./data/cache/patents/eapo
PATENT_CACHE_TTL_DAYS=7
PATENT_CACHE_ENABLED=true
```

---

## 4. Data Schemas

### 4.1 PatentQuery (Input)

```python
# app/schemas/ru_patent.py

from enum import Enum
from pydantic import BaseModel, Field

class MoleculeType(str, Enum):
    small_molecule = "small_molecule"
    biologic = "biologic"
    antibody = "antibody"
    combination = "combination"
    unknown = "unknown"

class PatentQuery(BaseModel):
    """Query for patent search across RU/EA sources."""
    
    inn: str
    inn_english: str | None = None
    inn_russian: str | None = None
    inn_synonyms: list[str] = Field(default_factory=list)
    brand_names: list[str] = Field(default_factory=list)
    molecular_target: str | None = None
    indication: str | None = None
    indication_synonyms: list[str] = Field(default_factory=list)
    known_assignees: list[str] = Field(default_factory=list)
    molecule_type: MoleculeType = MoleculeType.unknown
    ipc_codes: list[str] = Field(default_factory=list)
    cpc_codes: list[str] = Field(default_factory=list)
    max_results: int = 50
```

### 4.2 PatentEvidence (Normalized Output)

```python
class PatentType(str, Enum):
    composition_of_matter = "composition_of_matter"
    antibody_or_biologic_sequence = "antibody_or_biologic_sequence"
    salt_polymorph_or_crystal_form = "salt_polymorph_or_crystal_form"
    formulation = "formulation"
    method_of_manufacture = "method_of_manufacture"
    method_of_treatment_or_indication = "method_of_treatment_or_indication"
    dosing_regimen = "dosing_regimen"
    combination_therapy = "combination_therapy"
    biomarker_defined_subgroup = "biomarker_defined_subgroup"
    delivery_device = "delivery_device"
    process_or_intermediate = "process_or_intermediate"
    unknown = "unknown"

class LegalStatus(str, Enum):
    active = "active"
    expired = "expired"
    lapsed = "lapsed"
    terminated = "terminated"
    pending = "pending"
    withdrawn = "withdrawn"
    rejected = "rejected"
    unknown = "unknown"

class BlockingRisk(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"

class PatentEvidence(BaseModel):
    """Normalized patent evidence from any source.
    
    Per .clinerules/07-ru-eapo-patent-workflow.md requirements.
    """
    
    # Identifiers
    source_id: str  # e.g., "rospatent:RU2123456", "eapo:EA012345"
    source_type: str  # rospatent, fips, eapo, epo_ops, uspto, wipo
    jurisdiction: str  # RU, EA, EP, US, WO
    
    # Document numbers
    document_number: str
    application_number: str | None = None
    publication_number: str | None = None
    
    # Content
    title: str
    abstract: str | None = None
    claims_summary: str | None = None  # if available
    
    # Parties
    applicants: list[str] = Field(default_factory=list)
    patent_holders: list[str] = Field(default_factory=list)
    inventors: list[str] = Field(default_factory=list)
    
    # Dates
    filing_date: str | None = None
    priority_date: str | None = None
    publication_date: str | None = None
    grant_date: str | None = None
    
    # Status
    legal_status: LegalStatus = LegalStatus.unknown
    
    # Classification
    ipc_codes: list[str] = Field(default_factory=list)
    cpc_codes: list[str] = Field(default_factory=list)
    patent_types: list[PatentType] = Field(default_factory=list)
    
    # Analysis
    relevance_reason: str | None = None
    blocking_risk_preliminary: BlockingRisk = BlockingRisk.unknown
    
    # Provenance
    source_url: str | None = None
    retrieved_at: str
    raw_metadata: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
```

### 4.3 PatentFamilyEvidence (Clustered)

```python
class PatentFamilyEvidence(BaseModel):
    """Clustered patent family evidence."""
    
    family_id: str  # Generated or from INPADOC
    priority_number: str | None = None
    
    # Member patents
    members: list[PatentEvidence] = Field(default_factory=list)
    jurisdictions: list[str] = Field(default_factory=list)  # RU, EA, EP, US, etc.
    
    # Aggregated info
    earliest_priority_date: str | None = None
    latest_expiration_estimate: str | None = None
    main_applicants: list[str] = Field(default_factory=list)
    
    # Blocking assessment
    highest_blocking_risk: BlockingRisk = BlockingRisk.unknown
    blocking_jurisdictions: list[str] = Field(default_factory=list)
    
    # Analysis
    patent_types: list[PatentType] = Field(default_factory=list)
    relevance_summary: str | None = None
```

### 4.4 PatentSearchResult

```python
class PatentSearchResult(BaseModel):
    """Result of a patent search operation."""
    
    connector_name: str
    query: PatentQuery
    patents: list[PatentEvidence] = Field(default_factory=list)
    total_results_available: int = 0
    results_returned: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    source_available: bool = True

class AggregatedPatentResult(BaseModel):
    """Aggregated result from all patent sources."""
    
    query: PatentQuery
    
    # By source
    rospatent_results: PatentSearchResult | None = None
    fips_results: PatentSearchResult | None = None
    eapo_results: PatentSearchResult | None = None
    epo_results: PatentSearchResult | None = None
    wipo_results: PatentSearchResult | None = None
    
    # Aggregated & clustered
    all_patents: list[PatentEvidence] = Field(default_factory=list)
    patent_families: list[PatentFamilyEvidence] = Field(default_factory=list)
    
    # Diagnostics
    sources_queried: list[str] = Field(default_factory=list)
    sources_available: list[str] = Field(default_factory=list)
    sources_unavailable: list[str] = Field(default_factory=list)
    total_warnings: list[str] = Field(default_factory=list)
    requires_manual_review: bool = False
    manual_review_reasons: list[str] = Field(default_factory=list)
```

---

## 5. Source Priority Order

Per `.clinerules/07-ru-eapo-patent-workflow.md`:

| Priority | Source | Purpose | Fatal if unavailable? |
|----------|--------|---------|----------------------|
| 1 | Rospatent Open API | Primary RU patent discovery | No |
| 2 | Rospatent Open Data (cached) | Fallback for RU discovery | No |
| 3 | FIPS Search | Secondary RU discovery | No |
| 4 | FIPS Open Registers | Legal status verification | No |
| 5 | EAPO Registry | EA patent discovery | No |
| 6 | EAPO Bulletin | EA publication data | No |
| 7 | EAPATIS | Extended EA search (if accessible) | No |
| 8 | EPO OPS | International fallback + families | No |
| 9 | WIPO PATENTSCOPE | International fallback | No |
| 10 | Google Patents | Discovery fallback only | No |

**No source is fatal.** All failures are logged, warnings returned, manual review flagged.

---

## 6. Fallback Logic

```python
# app/connectors/patent_aggregator.py

class PatentAggregator:
    
    def search_all_sources(
        self, 
        query: PatentQuery, 
        *, 
        run_id: str,
        include_international: bool = True,
    ) -> AggregatedPatentResult:
        
        result = AggregatedPatentResult(query=query)
        
        # Step 1: Try Rospatent Open API
        rospatent_ok = self._try_rospatent_api(query, run_id, result)
        
        # Step 2: If Rospatent API failed, try cached data
        if not rospatent_ok:
            self._try_rospatent_cache(query, result)
        
        # Step 3: Try FIPS Search (always, for additional coverage)
        self._try_fips_search(query, run_id, result)
        
        # Step 4: Try EAPO Registry
        self._try_eapo_registry(query, run_id, result)
        
        # Step 5: International fallbacks (if enabled and credentials available)
        if include_international:
            self._try_epo_ops(query, run_id, result)
            self._try_wipo(query, run_id, result)
        
        # Step 6: Verify legal status for all found patents
        self._verify_legal_status_all(result, run_id)
        
        # Step 7: Cluster by family
        result.patent_families = self.cluster_by_family(result.all_patents)
        
        # Step 8: Check if manual review required
        if not result.sources_available:
            result.requires_manual_review = True
            result.manual_review_reasons.append(
                "No patent sources were available. Manual patent search required."
            )
        elif len(result.all_patents) == 0:
            result.requires_manual_review = True
            result.manual_review_reasons.append(
                "No patents found. This does not mean no patents exist. "
                "Manual verification required."
            )
        
        return result
    
    def _try_rospatent_api(self, query, run_id, result) -> bool:
        """Try Rospatent Open API. Returns True if successful."""
        try:
            connector = RospatentConnector()
            search_result = connector.search_patents(query, run_id=run_id)
            result.rospatent_results = search_result
            result.sources_queried.append("rospatent")
            
            if search_result.source_available:
                result.sources_available.append("rospatent")
                result.all_patents.extend(search_result.patents)
                return True
            else:
                result.sources_unavailable.append("rospatent")
                result.total_warnings.extend(search_result.warnings)
                return False
        except Exception as e:
            result.sources_queried.append("rospatent")
            result.sources_unavailable.append("rospatent")
            result.total_warnings.append(f"Rospatent API error: {e}")
            return False
    
    # Similar methods for other sources...
```

---

## 7. Caching Strategy

### 7.1 Cache Design

```python
# app/cache/patent_cache.py

from pathlib import Path
import json
import hashlib
from datetime import datetime, timedelta

class PatentCache:
    """Local cache for patent data.
    
    Structure:
    - data/cache/patents/ru/{hash}.json
    - data/cache/patents/eapo/{hash}.json
    - data/cache/patents/international/{hash}.json
    
    TTL-based invalidation for legal status data.
    """
    
    def __init__(self, cache_dir: Path, ttl_days: int = 7):
        self.cache_dir = cache_dir
        self.ttl = timedelta(days=ttl_days)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def get(self, key: str) -> dict | None:
        """Get cached entry if exists and not expired."""
        path = self._key_to_path(key)
        if not path.exists():
            return None
        
        entry = json.loads(path.read_text())
        cached_at = datetime.fromisoformat(entry["cached_at"])
        
        if datetime.now() - cached_at > self.ttl:
            return None  # Expired
        
        return entry["data"]
    
    def set(self, key: str, data: dict) -> None:
        """Store entry in cache."""
        path = self._key_to_path(key)
        entry = {
            "cached_at": datetime.now().isoformat(),
            "data": data,
        }
        path.write_text(json.dumps(entry, ensure_ascii=False, indent=2))
    
    def _key_to_path(self, key: str) -> Path:
        hash_key = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self.cache_dir / f"{hash_key}.json"
```

### 7.2 Cache Usage in Connectors

```python
class RospatentConnector(BaseRuPatentConnector):
    
    def __init__(self, cache: PatentCache | None = None):
        self.cache = cache or PatentCache(config.russian_patent_cache_dir)
    
    def search_patents(self, query: PatentQuery, *, run_id: str) -> PatentSearchResult:
        # Check cache first
        cache_key = f"rospatent:search:{query.inn}"
        cached = self.cache.get(cache_key)
        if cached:
            return PatentSearchResult(**cached)
        
        # Make API call
        result = self._search_api(query)
        
        # Cache successful results
        if result.source_available and result.patents:
            self.cache.set(cache_key, result.model_dump())
        
        return result
```

---

## 8. Audit Logging Design

### 8.1 Patent-Specific Audit Events

Extend `app/logging/audit_logger.py`:

```python
def log_patent_search(
    *,
    run_id: str,
    connector_name: str,
    source_name: str,
    endpoint: str,
    query_terms: list[str],
    http_method: str,
    status_code: int | None,
    latency_ms: int,
    result_count: int,
    warnings: list[str],
    errors: list[str],
) -> None:
    """Log a patent source search request."""
    log_event(
        AuditEvent(
            event_id=f"patent-search-{_now_iso()}",
            run_id=run_id,
            stage="patent_analysis",
            event_type="patent_search",
            timestamp=_now_iso(),
            status="succeeded" if not errors else "failed",
            metadata=_redact({
                "connector_name": connector_name,
                "source_name": source_name,
                "endpoint": endpoint,  # URL without secrets
                "query_terms": query_terms,
                "http_method": http_method,
                "status_code": status_code,
                "latency_ms": latency_ms,
                "result_count": result_count,
                "warnings": warnings,
                "errors": errors,
            }),
        )
    )

def log_patent_legal_status_check(
    *,
    run_id: str,
    connector_name: str,
    document_number: str,
    status_result: str,
    verification_source: str,
    warnings: list[str],
) -> None:
    """Log a patent legal status verification."""
    log_event(
        AuditEvent(
            event_id=f"patent-status-{_now_iso()}",
            run_id=run_id,
            stage="patent_analysis",
            event_type="patent_legal_status",
            timestamp=_now_iso(),
            status="succeeded",
            metadata={
                "connector_name": connector_name,
                "document_number": document_number,
                "status_result": status_result,
                "verification_source": verification_source,
                "warnings": warnings,
            },
        )
    )

def log_patent_source_unavailable(
    *,
    run_id: str,
    source_name: str,
    reason: str,
) -> None:
    """Log when a patent source is unavailable."""
    log_event(
        AuditEvent(
            event_id=f"patent-unavailable-{_now_iso()}",
            run_id=run_id,
            stage="patent_analysis",
            event_type="patent_source_unavailable",
            timestamp=_now_iso(),
            status="warning",
            metadata={
                "source_name": source_name,
                "reason": reason,
            },
        )
    )
```

### 8.2 Logging Fields per `.clinerules/07`

Every patent-source request logs:
- `run_id`
- `connector_name`
- `source_name`
- `endpoint` (URL without secrets)
- `query_terms`
- `http_method`
- `status_code`
- `latency_ms`
- `result_count`
- `warnings`
- `errors`
- `retrieved_at`

**Never logged:** API keys, session cookies, credentials, secrets.

---

## 9. Testing Strategy

### 9.1 Test Categories

| Category | Description | Mock Strategy |
|----------|-------------|---------------|
| Schema validation | PatentEvidence, PatentFamilyEvidence, PatentQuery | Unit tests |
| Rospatent connector | API calls, response parsing | Mocked HTTP responses |
| FIPS connector | Search + registers | Mocked HTTP responses |
| EAPO connector | Registry lookup | Mocked HTTP responses |
| Cache layer | Get/set, TTL expiration | Temp directories |
| Aggregator | Fallback logic, clustering | Mocked connectors |
| Legal status | Status verification | Mocked registry responses |
| Audit logging | Event structure | Captured log output |

### 9.2 Test Fixtures

```text
tests/connectors/ru_patent/fixtures/
├── rospatent_search_response.json       # Valid search response
├── rospatent_patent_detail.json         # Single patent detail
├── rospatent_empty_response.json        # No results
├── rospatent_error_response.json        # API error
├── fips_search_response.json            # FIPS search result
├── fips_registers_active.json           # Active patent status
├── fips_registers_expired.json          # Expired patent status
├── fips_registers_not_found.json        # Patent not in registry

tests/connectors/eapo/fixtures/
├── eapo_registry_response.json          # EAPO search result
├── eapo_patent_detail.json              # Single EA patent
├── eapo_empty_response.json             # No results
```

### 9.3 Example Test Structure

```python
# tests/connectors/ru_patent/test_rospatent.py

import pytest
from unittest.mock import Mock, patch
from app.connectors.ru_patent.rospatent import RospatentConnector
from app.schemas.ru_patent import PatentQuery, LegalStatus

class TestRospatentConnector:
    
    @pytest.fixture
    def connector(self):
        return RospatentConnector(http_client=Mock())
    
    @pytest.fixture
    def sample_query(self):
        return PatentQuery(
            inn="ibuprofen",
            inn_russian="ибупрофен",
            inn_synonyms=["ibuprofenum"],
        )
    
    def test_search_returns_valid_results(self, connector, sample_query):
        """Test successful patent search."""
        with patch.object(connector, '_search_api') as mock_search:
            mock_search.return_value = self._load_fixture("rospatent_search_response.json")
            
            result = connector.search_patents(sample_query, run_id="test-run")
            
            assert result.source_available is True
            assert len(result.patents) > 0
            assert all(p.jurisdiction == "RU" for p in result.patents)
    
    def test_search_handles_empty_response(self, connector, sample_query):
        """Test handling of no results."""
        with patch.object(connector, '_search_api') as mock_search:
            mock_search.return_value = self._load_fixture("rospatent_empty_response.json")
            
            result = connector.search_patents(sample_query, run_id="test-run")
            
            assert result.source_available is True
            assert len(result.patents) == 0
            assert len(result.warnings) > 0
    
    def test_search_handles_api_error(self, connector, sample_query):
        """Test graceful handling of API errors."""
        with patch.object(connector, '_search_api') as mock_search:
            mock_search.side_effect = Exception("Connection timeout")
            
            result = connector.search_patents(sample_query, run_id="test-run")
            
            assert result.source_available is False
            assert len(result.errors) > 0
    
    def test_legal_status_active(self, connector):
        """Test legal status verification for active patent."""
        with patch.object(connector, 'get_legal_status') as mock_status:
            mock_status.return_value = self._load_fixture("fips_registers_active.json")
            
            patent = connector.get_legal_status("RU2123456", run_id="test-run")
            
            assert patent.legal_status == LegalStatus.active
    
    @staticmethod
    def _load_fixture(name: str) -> dict:
        import json
        from pathlib import Path
        fixture_path = Path(__file__).parent / "fixtures" / name
        return json.loads(fixture_path.read_text())
```

### 9.4 Aggregator Tests

```python
# tests/connectors/test_patent_aggregator.py

class TestPatentAggregator:
    
    def test_fallback_to_cache_when_api_fails(self):
        """Test fallback to cached data when Rospatent API fails."""
        ...
    
    def test_continues_when_one_source_unavailable(self):
        """Test that pipeline continues when one source is down."""
        ...
    
    def test_manual_review_flagged_when_all_sources_fail(self):
        """Test that manual review is required when no sources available."""
        ...
    
    def test_family_clustering(self):
        """Test patent family clustering logic."""
        ...
    
    def test_legal_status_verified_for_all_patents(self):
        """Test that legal status is verified for found patents."""
        ...
```

---

## 10. Open Risks and Assumptions

### 10.1 Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Rospatent API unstable or requires registration | Medium | Cache-first design, FIPS fallback |
| FIPS does not have structured API | High | Document-by-number lookup only, manual review flag |
| EAPO Registry may not have REST API | High | Bulletin parsing, EPO OPS for EA patents |
| Legal status data may be incomplete | Medium | `unknown` status + warning + manual review flag |
| Rate limiting on all sources | Medium | Caching, configurable delays |
| HTML scraping unstable | High | Avoided per requirements; use official APIs only |

### 10.2 Assumptions

1. **Rospatent Open API exists and is accessible** — will need to verify actual API availability
2. **FIPS Registers support document-by-number lookup** — may require reverse-engineering
3. **EAPO has some form of structured data access** — may fall back to EPO OPS for EA patents
4. **EPO OPS credentials will be configured** — existing connector can be reused
5. **Cache directory will have write permissions**
6. **All sources return at least patent number, title, and applicant**

### 10.3 Dependencies on Existing Code

- `BaseConnector` from `app/connectors/base.py`
- `log_tool_call`, `log_event` from `app/logging/audit_logger.py`
- `config` from `app/config.py`
- `SourceType`, `EvidenceCategory` enums from `app/schemas/evidence.py`
- Existing `EPOOPSConnector` from `app/connectors/epo_ops.py`
- Existing `WIPOConnector` from `app/connectors/wipo.py`

### 10.4 Not in Scope

- Full EAPATIS integration (marked as optional)
- Google Patents integration beyond URL generation
- Automated patent claim analysis
- Automated FTO conclusions (always requires manual review)
- Patent image extraction

---

## 11. Implementation Order

1. **Phase 1: Schemas**
   - Add `app/schemas/ru_patent.py` with all data models
   - Update `app/schemas/evidence.py` with new SourceTypes

2. **Phase 2: Cache Layer**
   - Create `app/cache/patent_cache.py`
   - Add cache configuration to `app/config.py`

3. **Phase 3: Rospatent Connector**
   - Create `app/connectors/ru_patent/rospatent.py`
   - Add tests with mocked responses

4. **Phase 4: FIPS Connectors**
   - Create `app/connectors/ru_patent/fips_search.py`
   - Create `app/connectors/ru_patent/fips_registers.py`
   - Add tests

5. **Phase 5: EAPO Connector**
   - Create `app/connectors/eapo/eapo_registry.py`
   - Add tests

6. **Phase 6: Patent Aggregator**
   - Create `app/connectors/patent_aggregator.py`
   - Implement fallback logic
   - Implement family clustering
   - Add tests

7. **Phase 7: Audit Logging**
   - Add patent-specific logging functions
   - Update audit schema if needed

8. **Phase 8: Integration**
   - Integrate aggregator with `patent_finance_agent.py`
   - Update orchestrator to use patent aggregator

9. **Phase 9: Documentation**
   - Update README.md
   - Update .env.example

---

## 12. Required Disclaimers

All outputs must include:

**English:**
> "This automated patent analysis is preliminary and does not constitute a legal freedom-to-operate opinion. The results must be reviewed by a qualified patent attorney before any development, licensing, or commercialization decision."

**Russian:**
> "Данный автоматизированный патентный анализ является предварительным и не является юридическим заключением о свободе действий. Результаты должны быть проверены квалифицированным патентным поверенным до принятия решений о разработке, лицензировании или коммерциализации."

---

## Approval Request

Please review this implementation plan and confirm:

1. ✅ Folder structure is acceptable
2. ✅ Connector interfaces are correct
3. ✅ Schema design meets requirements
4. ✅ Fallback logic is appropriate
5. ✅ Testing strategy is sufficient
6. ✅ Open risks are understood

**Ready to proceed with implementation after approval.**
