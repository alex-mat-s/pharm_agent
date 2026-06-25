"""Healthcheck service: verify connectivity and readiness of all backend components."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import config

# Ensure directories exist on module load
config.ensure_dirs()


def _get_proxy_transport() -> httpx.HTTPTransport | None:
    """Get HTTP transport with proxy if configured."""
    proxy_url = config.fda_proxy_url
    if proxy_url:
        return httpx.HTTPTransport(proxy=proxy_url)
    return None


def _make_proxied_request(url: str, timeout: int = 10, **kwargs) -> httpx.Response:
    """Make HTTP request using proxy if configured."""
    proxy_url = config.fda_proxy_url
    if proxy_url:
        with httpx.Client(proxy=proxy_url, timeout=timeout) as client:
            return client.get(url, **kwargs)
    return httpx.get(url, timeout=timeout, **kwargs)


@dataclass
class HealthStatus:
    name: str
    ok: bool
    detail: str = ""
    fatal: bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# Core Services
# ═══════════════════════════════════════════════════════════════════════════════

def check_openrouter() -> HealthStatus:
    key = os.environ.get("OPENROUTER_API_KEY") or config.openrouter_api_key
    if not key:
        return HealthStatus("OpenRouter", False, "OPENROUTER_API_KEY not set", fatal=True)
    try:
        r = httpx.get(
            f"{config.openrouter_base_url}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if r.status_code == 200:
            return HealthStatus("OpenRouter", True, "OK")
        return HealthStatus("OpenRouter", False, f"HTTP {r.status_code}", fatal=True)
    except Exception as exc:
        return HealthStatus("OpenRouter", False, str(exc), fatal=True)


def check_sqlite() -> HealthStatus:
    """Check SQLite database availability. Initializes DB if not exists."""
    db_path = config.db_path
    try:
        from app.storage.db import Database
        db = Database(db_path)
        db.init_schema()  # Creates DB and tables if not exist
        return HealthStatus("SQLite", True, str(db_path))
    except Exception as exc:
        return HealthStatus("SQLite", False, f"Error: {exc}", fatal=True)


def check_vault() -> HealthStatus:
    vault = config.vault_dir
    if vault.exists() and vault.is_dir():
        try:
            test_file = vault / ".healthcheck_test"
            test_file.write_text("ok")
            test_file.unlink()
            return HealthStatus("Obsidian vault", True, str(vault))
        except OSError as exc:
            return HealthStatus("Obsidian vault", False, f"Not writable: {exc}")
    return HealthStatus("Obsidian vault", False, f"Directory not found: {vault}")


def check_pdf_dir() -> HealthStatus:
    pdf_dir = config.pdfs_dir
    if pdf_dir.exists() and pdf_dir.is_dir():
        return HealthStatus("PDF directory", True, str(pdf_dir))
    return HealthStatus("PDF directory", False, f"Not found: {pdf_dir}")


def check_audit_log() -> HealthStatus:
    log_path = config.logs_dir / "audit.jsonl"
    if log_path.exists():
        return HealthStatus("Audit log", True, str(log_path))
    if config.logs_dir.exists():
        return HealthStatus("Audit log", True, f"Will be created: {log_path}")
    return HealthStatus("Audit log", False, f"Logs directory not found: {config.logs_dir}")


# ═══════════════════════════════════════════════════════════════════════════════
# Scientific / Clinical Sources
# ═══════════════════════════════════════════════════════════════════════════════

def check_pubmed() -> HealthStatus:
    try:
        r = httpx.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?retmode=json",
            timeout=10,
        )
        if r.status_code == 200:
            return HealthStatus("PubMed / NCBI", True, "OK")
        return HealthStatus("PubMed / NCBI", False, f"HTTP {r.status_code}")
    except Exception as exc:
        return HealthStatus("PubMed / NCBI", False, str(exc))


def check_clinicaltrials() -> HealthStatus:
    try:
        r = httpx.get(
            "https://clinicaltrials.gov/api/v2/studies?query.term=test&pageSize=1",
            timeout=10,
        )
        if r.status_code == 200:
            return HealthStatus("ClinicalTrials.gov", True, "OK")
        return HealthStatus("ClinicalTrials.gov", False, f"HTTP {r.status_code}")
    except Exception as exc:
        return HealthStatus("ClinicalTrials.gov", False, str(exc))


def check_openfda() -> HealthStatus:
    """Check openFDA API, using proxy if configured."""
    proxy_note = " (via proxy)" if config.fda_proxy_url else ""
    try:
        r = _make_proxied_request(
            "https://api.fda.gov/drug/label.json?search=openfda.generic_name:aspirin&limit=1",
            timeout=10,
        )
        if r.status_code == 200:
            return HealthStatus("openFDA", True, f"OK{proxy_note}")
        if r.status_code == 403:
            return HealthStatus("openFDA", False, f"HTTP 403 — access blocked{proxy_note}")
        return HealthStatus("openFDA", False, f"HTTP {r.status_code}{proxy_note}")
    except Exception as exc:
        return HealthStatus("openFDA", False, f"{exc}{proxy_note}")


def check_ema() -> HealthStatus:
    """Check EMA medicines JSON availability (same URL as EMAConnector)."""
    # Use the same URL as in app/connectors/ema.py
    ema_json_url = (
        "https://www.ema.europa.eu/en/documents/report/"
        "medicines-output-medicines_json-report_en.json"
    )
    cache_path = Path("data/cache/ema_medicines_full.json")
    
    try:
        r = httpx.get(ema_json_url, timeout=20, follow_redirects=True)
        if r.status_code == 200:
            # Try to parse JSON to verify
            try:
                data = r.json()
                if isinstance(data, list) and len(data) > 0:
                    return HealthStatus("EMA", True, f"OK ({len(data)} medicines)")
                elif isinstance(data, dict):
                    # EMA may wrap data in a dict
                    return HealthStatus("EMA", True, "OK (JSON valid)")
                return HealthStatus("EMA", True, "OK")
            except Exception:
                return HealthStatus("EMA", True, "OK (HTTP 200, JSON not validated)")
        detail = f"HTTP {r.status_code}"
    except Exception as exc:
        detail = str(exc)

    if cache_path.exists():
        return HealthStatus("EMA", True, f"Live unavailable ({detail}), using cache")
    return HealthStatus("EMA", False, f"Live unavailable ({detail}), cache not found")


# ═══════════════════════════════════════════════════════════════════════════════
# Patent Sources — US
# ═══════════════════════════════════════════════════════════════════════════════

def check_orange_book() -> HealthStatus:
    """Check FDA Orange Book API availability, using proxy if configured."""
    proxy_note = " (via proxy)" if config.fda_proxy_url else ""
    try:
        r = _make_proxied_request(
            "https://api.fda.gov/drug/drugsfda.json?search=products.brand_name:aspirin&limit=1",
            timeout=10,
        )
        if r.status_code == 200:
            return HealthStatus("Orange Book (FDA)", True, f"OK{proxy_note}")
        if r.status_code == 403:
            return HealthStatus("Orange Book (FDA)", False, f"HTTP 403 — access blocked{proxy_note}")
        return HealthStatus("Orange Book (FDA)", False, f"HTTP {r.status_code}{proxy_note}")
    except Exception as exc:
        return HealthStatus("Orange Book (FDA)", False, f"{exc}{proxy_note}")


def check_purple_book() -> HealthStatus:
    """Check FDA Purple Book API availability."""
    try:
        r = httpx.get(
            "https://purplebooksearch.fda.gov/api/v1/products?query=adalimumab&limit=1",
            timeout=10,
        )
        if r.status_code == 200:
            return HealthStatus("Purple Book (FDA)", True, "OK")
        if r.status_code == 404:
            # Purple Book API may return 404 for empty results
            return HealthStatus("Purple Book (FDA)", True, "OK (empty result)")
        return HealthStatus("Purple Book (FDA)", False, f"HTTP {r.status_code}")
    except Exception as exc:
        return HealthStatus("Purple Book (FDA)", False, str(exc))


def check_uspto() -> HealthStatus:
    """Check USPTO PatentsView API availability."""
    try:
        r = httpx.get(
            "https://api.patentsview.org/patents/query?q={\"patent_number\":\"10000000\"}&f=[\"patent_title\"]",
            timeout=10,
        )
        if r.status_code == 200:
            return HealthStatus("USPTO PatentsView", True, "OK")
        return HealthStatus("USPTO PatentsView", False, f"HTTP {r.status_code}")
    except Exception as exc:
        return HealthStatus("USPTO PatentsView", False, str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# Patent Sources — International
# ═══════════════════════════════════════════════════════════════════════════════

def check_epo_ops() -> HealthStatus:
    """Check EPO Open Patent Services availability (requires credentials)."""
    consumer_key = os.environ.get("EPO_OPS_CONSUMER_KEY") or getattr(config, "epo_ops_consumer_key", None)
    consumer_secret = os.environ.get("EPO_OPS_CONSUMER_SECRET") or getattr(config, "epo_ops_consumer_secret", None)
    
    if not consumer_key or not consumer_secret:
        return HealthStatus("EPO OPS", False, "EPO_OPS_CONSUMER_KEY/SECRET not set (optional)", fatal=False)
    
    try:
        # Try to get access token
        import base64
        credentials = base64.b64encode(f"{consumer_key}:{consumer_secret}".encode()).decode()
        r = httpx.post(
            "https://ops.epo.org/3.2/auth/accesstoken",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data="grant_type=client_credentials",
            timeout=10,
        )
        if r.status_code == 200:
            return HealthStatus("EPO OPS", True, "OK (authenticated)")
        return HealthStatus("EPO OPS", False, f"HTTP {r.status_code}")
    except Exception as exc:
        return HealthStatus("EPO OPS", False, str(exc))


def check_wipo() -> HealthStatus:
    """Check WIPO PatentScope availability.
    
    NOTE: WIPO does NOT have a free public REST API.
    The web UI blocks automated access with 403.
    This is expected behavior - connector returns source_unavailable.
    """
    # WIPO Patentscope does not have a free public REST API
    # The result.jsf pages return HTML and block scraping with 403
    # See app/connectors/wipo.py for details
    return HealthStatus(
        "WIPO PatentScope", 
        False, 
        "No public API (expected). Use EPO OPS or Google Patents instead.",
        fatal=False  # Not fatal - run continues without WIPO
    )


def check_google_patents() -> HealthStatus:
    """Check Google Patents availability."""
    try:
        r = httpx.get(
            "https://patents.google.com/",
            timeout=10,
            follow_redirects=True,
        )
        if r.status_code == 200:
            return HealthStatus("Google Patents", True, "OK")
        return HealthStatus("Google Patents", False, f"HTTP {r.status_code}")
    except Exception as exc:
        return HealthStatus("Google Patents", False, str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# Patent Sources — Russian / Eurasian
# ═══════════════════════════════════════════════════════════════════════════════

def check_rospatent() -> HealthStatus:
    """Check Rospatent Open API availability with API key if configured."""
    api_key = config.rospatent_api_key
    if not api_key:
        return HealthStatus("Rospatent API", False, "ROSPATENT_API_KEY not set (optional)", fatal=False)
    
    try:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        # Rospatent Search API requires POST with JSON body
        payload = {
            "q": "aspirin",
            "limit": 1,
            "offset": 0,
        }
        r = httpx.post(
            f"{config.rospatent_base_url}/patsearch/v0.2/search",
            timeout=15,
            headers=headers,
            json=payload,
        )
        if r.status_code == 200:
            return HealthStatus("Rospatent API", True, "OK (authenticated)")
        if r.status_code == 401:
            return HealthStatus("Rospatent API", False, "HTTP 401 — invalid API key")
        if r.status_code == 403:
            return HealthStatus("Rospatent API", False, "HTTP 403 — access blocked")
        return HealthStatus("Rospatent API", False, f"HTTP {r.status_code}")
    except Exception as exc:
        return HealthStatus("Rospatent API", False, str(exc))


def check_fips() -> HealthStatus:
    """Check FIPS (Russian patent info) availability."""
    try:
        r = httpx.get(
            "https://www.fips.ru/",
            timeout=10,
            follow_redirects=True,
        )
        if r.status_code == 200:
            return HealthStatus("FIPS", True, "OK")
        return HealthStatus("FIPS", False, f"HTTP {r.status_code}")
    except Exception as exc:
        return HealthStatus("FIPS", False, str(exc))


def check_eapo() -> HealthStatus:
    """Check EAPO (Eurasian Patent Organization) registry availability."""
    try:
        r = httpx.get(
            "https://www.eapo.org/ru/",
            timeout=10,
            follow_redirects=True,
        )
        if r.status_code == 200:
            return HealthStatus("EAPO Registry", True, "OK")
        return HealthStatus("EAPO Registry", False, f"HTTP {r.status_code}")
    except Exception as exc:
        return HealthStatus("EAPO Registry", False, str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# Patent Cache
# ═══════════════════════════════════════════════════════════════════════════════

def check_patent_cache() -> HealthStatus:
    """Check patent cache directory status."""
    cache_dir = Path("data/cache/patents")
    ru_cache = cache_dir / "ru"
    eapo_cache = cache_dir / "eapo"
    
    if not cache_dir.exists():
        return HealthStatus("Patent Cache", False, f"Directory not found: {cache_dir}")
    
    ru_count = len(list(ru_cache.glob("*.json"))) if ru_cache.exists() else 0
    eapo_count = len(list(eapo_cache.glob("*.json"))) if eapo_cache.exists() else 0
    
    return HealthStatus(
        "Patent Cache", 
        True, 
        f"RU: {ru_count} entries, EAPO: {eapo_count} entries"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Main function
# ═══════════════════════════════════════════════════════════════════════════════

# All checks with their display names and categories
HEALTHCHECK_ITEMS = [
    # Core services
    ("Core: OpenRouter", check_openrouter),
    ("Core: SQLite", check_sqlite),
    ("Core: Obsidian Vault", check_vault),
    ("Core: PDF Directory", check_pdf_dir),
    ("Core: Audit Log", check_audit_log),
    
    # Scientific / Clinical sources
    ("Science: PubMed", check_pubmed),
    ("Science: ClinicalTrials.gov", check_clinicaltrials),
    ("Science: openFDA", check_openfda),
    ("Science: EMA", check_ema),
    
    # Patent sources — US
    ("Patents US: Orange Book", check_orange_book),
    ("Patents US: Purple Book", check_purple_book),
    ("Patents US: USPTO", check_uspto),
    
    # Patent sources — International
    ("Patents Intl: EPO OPS", check_epo_ops),
    ("Patents Intl: WIPO", check_wipo),
    ("Patents Intl: Google Patents", check_google_patents),
    
    # Patent sources — Russian / Eurasian
    ("Patents RU/EA: Rospatent", check_rospatent),
    ("Patents RU/EA: FIPS", check_fips),
    ("Patents RU/EA: EAPO", check_eapo),
    
    # Patent cache
    ("Cache: Patent Cache", check_patent_cache),
]


def run_all_checks() -> list[HealthStatus]:
    """Run all healthchecks and return results."""
    results = []
    for _name, check_fn in HEALTHCHECK_ITEMS:
        results.append(check_fn())
    return results


def run_all_checks_streaming():
    """Generator that yields (progress_pct, current_item, partial_results) as checks complete.
    
    Yields:
        tuple[int, str, list[HealthStatus]]: (progress percentage, current check name, results so far)
    """
    total = len(HEALTHCHECK_ITEMS)
    results: list[HealthStatus] = []
    
    for i, (name, check_fn) in enumerate(HEALTHCHECK_ITEMS):
        progress_pct = int((i / total) * 100)
        yield progress_pct, f"Checking {name}...", results.copy()
        
        result = check_fn()
        results.append(result)
    
    # Final yield with 100% progress
    yield 100, "Complete", results
