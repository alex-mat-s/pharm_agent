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
