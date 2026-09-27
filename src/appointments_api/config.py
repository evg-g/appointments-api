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

    # SQLAlchemy/psycopg URL. psycopg (v3) drives both the async app engine and the
    # synchronous Alembic migrations, so one driver covers both.
    database_url: str = "postgresql+psycopg://aurora:aurora@localhost:5432/aurora"

    # Redis backs refresh-token rotation/reuse detection (and, from milestone 4, idempotency
    # keys and rate limiting).
    redis_url: str = "redis://localhost:6379/0"

    # Auth. The default secret is for local/CI only; production must override it via the
    # environment. Access tokens are short-lived; refresh tokens are long-lived but single-use
    # (rotated on every refresh, with reuse detection).
    jwt_secret: str = "dev-only-insecure-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 15 * 60  # 15 minutes
    refresh_token_ttl_seconds: int = 14 * 24 * 60 * 60  # 14 days


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once."""
    return Settings()
