"""Healthcheck service: verify connectivity and readiness of all backend components."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import config


@dataclass
class HealthStatus:
    name: str
    ok: bool
    detail: str = ""
    fatal: bool = False


def check_openrouter() -> HealthStatus:
    key = os.environ.get("OPENROUTER_API_KEY") or config.openrouter_api_key
    if not key:
        return HealthStatus("OpenRouter", False, "OPENROUTER_API_KEY не задан", fatal=True)
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
    try:
        r = httpx.get(
            "https://api.fda.gov/drug/label.json?search=openfda.generic_name:aspirin&limit=1",
            timeout=10,
        )
        if r.status_code == 200:
            return HealthStatus("openFDA", True, "OK")
        if r.status_code == 403:
            return HealthStatus("openFDA", False, "HTTP 403 — доступ заблокирован (геоблокировка?)")
        return HealthStatus("openFDA", False, f"HTTP {r.status_code}")
    except Exception as exc:
        return HealthStatus("openFDA", False, str(exc))


def check_ema() -> HealthStatus:
    cache_path = Path("app/connectors/ema_cache.json")
    try:
        r = httpx.get(
            "https://medicines.ema.europa.eu/documents/ema-json/medicines.json",
            timeout=15,
        )
        if r.status_code == 200:
            return HealthStatus("EMA", True, "Live JSON доступен")
        detail = f"HTTP {r.status_code}"
    except Exception as exc:
        detail = str(exc)

    if cache_path.exists():
        return HealthStatus("EMA", True, f"Live недоступен ({detail}), используется кеш")
    return HealthStatus("EMA", False, f"Live недоступен ({detail}), кеш не найден")


def check_sqlite() -> HealthStatus:
    db_path = config.db_path
    if db_path.exists():
        return HealthStatus("SQLite", True, str(db_path))
    return HealthStatus("SQLite", False, f"Файл не найден: {db_path}", fatal=True)


def check_vault() -> HealthStatus:
    vault = config.vault_dir
    if vault.exists() and vault.is_dir():
        try:
            test_file = vault / ".healthcheck_test"
            test_file.write_text("ok")
            test_file.unlink()
            return HealthStatus("Obsidian vault", True, str(vault))
        except OSError as exc:
            return HealthStatus("Obsidian vault", False, f"Не записывается: {exc}")
    return HealthStatus("Obsidian vault", False, f"Директория не найдена: {vault}")


def check_pdf_dir() -> HealthStatus:
    pdf_dir = config.pdfs_dir
    if pdf_dir.exists() and pdf_dir.is_dir():
        return HealthStatus("PDF директория", True, str(pdf_dir))
    return HealthStatus("PDF директория", False, f"Не найдена: {pdf_dir}")


def check_audit_log() -> HealthStatus:
    log_path = config.logs_dir / "audit.jsonl"
    if log_path.exists():
        return HealthStatus("Audit log", True, str(log_path))
    if config.logs_dir.exists():
        return HealthStatus("Audit log", True, f"Будет создан: {log_path}")
    return HealthStatus("Audit log", False, f"Директория logs не найдена: {config.logs_dir}")


def run_all_checks() -> list[HealthStatus]:
    """Run all healthchecks and return results."""
    return [
        check_openrouter(),
        check_pubmed(),
        check_clinicaltrials(),
        check_openfda(),
        check_ema(),
        check_sqlite(),
        check_vault(),
        check_pdf_dir(),
        check_audit_log(),
    ]
