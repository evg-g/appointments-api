"""Application settings, loaded from the environment and validated at startup."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    LOCAL = "local"
    CI = "ci"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Twelve-factor settings. Unknown env vars are ignored; types are validated."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: AppEnv = AppEnv.LOCAL
    app_log_level: str = "info"

    # SQLAlchemy/psycopg URL. psycopg (v3) drives both the async app engine (added in
    # milestone 3) and the synchronous Alembic migrations, so one driver covers both.
    database_url: str = "postgresql+psycopg://aurora:aurora@localhost:5432/aurora"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once."""
    return Settings()
