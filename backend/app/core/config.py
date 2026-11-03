from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    app_name: str = "OrderBridge"
    app_version: str = "0.1.0"
    app_env: Literal["development", "testing", "production"] = "development"

    database_url: SecretStr = Field(repr=False)

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT.parent / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached application settings instance."""
    return Settings()


settings = get_settings()
