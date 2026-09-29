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

    # Idempotency-Key (milestone 4). A stored create response is replayed for this long, so a
    # client that retries after a network blip gets the original resource, never a duplicate.
    idempotency_ttl_seconds: int = 24 * 60 * 60  # 24 hours

    # Rate limiting (milestone 4). Fixed window per principal. The default is generous; a low
    # value is used in the dedicated rate-limit test. Set enabled=False to turn it off entirely.
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 300
    rate_limit_window_seconds: int = 60

    # Webhooks (milestone 4). State changes are signed with HMAC-SHA256 and delivered by a
    # background worker with exponential backoff; a job that keeps failing is dead-lettered.
    webhook_delivery_timeout_seconds: float = 5.0
    webhook_max_attempts: int = 5
    webhook_backoff_base_seconds: float = 1.0
    webhook_backoff_cap_seconds: float = 300.0
    webhook_signature_tolerance_seconds: int = 300
    # The in-process worker is off by default so tests and `make dev` stay deterministic; run it
    # explicitly with `python -m appointments_api.workers.webhooks`, or set this true.
    webhook_worker_enabled: bool = False

    # Telemetry ingestion (milestone 10). A reading whose device clock differs from the server clock
    # by more than the skew threshold is stored but flagged; one dated further than the future
    # tolerance ahead of the server clock is rejected outright (spec §4 rule 10).
    telemetry_max_skew_seconds: float = 300.0
    telemetry_future_tolerance_seconds: float = 60.0

    # MQTT ingestion worker (milestone 10). The worker subscribes to the broker as the primary path;
    # the HTTP batch endpoint is the fallback. Both funnel into the same ingestion service. The
    # worker runs as a separate process (`python -m appointments_api.workers.telemetry_mqtt`); it is
    # never started in-process, so tests and `make dev` need no broker.
    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    # Topic filter for inbound telemetry. Scheme (spec §8):
    #   aurora/v1/clinic/{clinic_id}/device/{device_id}/telemetry
    mqtt_topic_filter: str = "aurora/v1/clinic/+/device/+/telemetry"
    mqtt_client_id: str = "appointments-api-ingestion"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, parsed once."""
    return Settings()
