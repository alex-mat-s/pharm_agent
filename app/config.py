from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Application settings loaded from .env and environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    default_openrouter_model: str = "openai/gpt-4o-mini"
    llm_timeout_seconds: int = 300

    # Patent data connectors (optional)
    epo_ops_consumer_key: str | None = None
    epo_ops_consumer_secret: str | None = None
    
    # USPTO Open Data Portal API (optional)
    # The legacy PatentsView endpoint is deprecated and returns 301
    # Get API key from: https://developer.uspto.gov/
    uspto_odp_api_key: str | None = None

    # FDA connector settings (proxy + API)
    fda_api_url: str = "https://api.fda.gov/drug/drugsfda.json"
    fda_proxy_url: str | None = None
    fda_api_key: str | None = None

    # Russian Patent Sources
    rospatent_base_url: str = "https://searchplatform.rospatent.gov.ru"
    rospatent_api_key: str | None = None
    fips_base_url: str = "https://www.fips.ru"
    fips_registers_base_url: str = "https://new.fips.ru/registers"

    # EAPO Sources
    eapo_base_url: str = "https://www.eapo.org"
    eapo_registry_url: str = "https://www.eapo.org/ru/publications/publicat/index_search_new.php"

    # International Fallbacks
    wipo_patentscope_base_url: str = "https://patentscope.wipo.int"
    google_patents_base_url: str = "https://patents.google.com"

    # Patent Cache Configuration
    patent_cache_dir: Path = Path("./data/cache/patents")
    russian_patent_cache_dir: Path = Path("./data/cache/patents/ru")
    eapo_patent_cache_dir: Path = Path("./data/cache/patents/eapo")
    patent_cache_ttl_days: int = 7
    patent_cache_enabled: bool = True

    debug: bool = False

    pdfs_dir: Path = Path("./pdfs")
    vault_dir: Path = Path("./vault")
    logs_dir: Path = Path("./logs")
    db_path: Path = Path("./runs.sqlite")

    def ensure_dirs(self) -> None:
        """Create directories if they do not exist."""
        for d in (self.pdfs_dir, self.vault_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


# Singleton-like instance; tests can replace this with a new Config instance.
config = Config()
