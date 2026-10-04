"""Integration-test fixtures: real Postgres + Redis via testcontainers, no mocks.

The integration tier deliberately uses no test doubles (spec §5): it runs the real app against a
real database (so the exclusion constraint is exercised) and real Redis (so refresh-token rotation
is exercised). Containers are session-scoped; each test starts from a truncated database and a
flushed Redis.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Literal

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from appointments_api.config import Settings, get_settings
from appointments_api.main import create_app
from appointments_api.security import hash_password

_TABLES = (
    "telemetry_readings",
    "excursions",
    "threshold_policies",
    "devices",
    "webhook_subscriptions",
    "audit_log",
    "appointments",
    "clinician_working_hours",
    "clinicians",
    "services",
    "clinics",
    "users",
)


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16", driver="psycopg") as container:
        yield container


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    with RedisContainer("redis:7") as container:
        yield container


@pytest.fixture(scope="session")
def settings(postgres: PostgresContainer, redis_container: RedisContainer) -> Settings:
    db_url = postgres.get_connection_url()
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    redis_url = f"redis://{host}:{port}/0"
    return Settings(
        database_url=db_url,
        redis_url=redis_url,
        jwt_secret="test-secret-please-change-32-bytes-minimum",
        access_token_ttl_seconds=900,
        refresh_token_ttl_seconds=3600,
    )


@pytest.fixture(scope="session", autouse=True)
def _migrate(settings: Settings) -> None:
    # env.py reads the URL from get_settings(); point it at the container, then upgrade.
    os.environ["DATABASE_URL"] = settings.database_url
    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    engine = application.state.engine
    redis = application.state.redis
    # Clean slate for each test.
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
    await redis.flushdb()
    try:
        yield application
    finally:
        await engine.dispose()
        await redis.aclose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def raw_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """A client that returns the app's 500 response instead of re-raising the app's exception."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ---- forcing a failed COMMIT (ADR 0017) ----

CommitFailureMode = Literal["error", "integrity"]
TriggerEvent = Literal["INSERT", "UPDATE", "DELETE"]

# SQLSTATE raised at COMMIT. P0001 (raise_exception) surfaces as a general database error;
# 23514 (check_violation) is an integrity-constraint class, so SQLAlchemy raises IntegrityError.
_SQLSTATE: dict[CommitFailureMode, str] = {"error": "P0001", "integrity": "23514"}


class CommitFailure:
    """Installs test-only deferred constraint triggers that make a COMMIT fail for one chosen row.

    A ``DEFERRABLE INITIALLY DEFERRED`` constraint trigger runs at COMMIT time, not at the
    statement, so the request handler runs to the end (every write flushed) and only the COMMIT
    is refused. ``when`` is a SQL boolean over ``NEW`` (INSERT/UPDATE) or ``OLD`` (DELETE) that
    picks the one row; callers inline only validated values (UUIDs, fixed literals).
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._installed: list[tuple[str, str]] = []

    async def arm(
        self,
        *,
        table: str,
        event: TriggerEvent,
        when: str,
        mode: CommitFailureMode = "error",
    ) -> None:
        name = f"test_fail_commit_{len(self._installed)}"
        row = "OLD" if event == "DELETE" else "NEW"
        # Register before creating, so teardown drops a half-installed trigger as well.
        self._installed.append((name, table))
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                    f"BEGIN IF {when} THEN RAISE EXCEPTION 'test: commit refused ({name})' "
                    f"USING ERRCODE = '{_SQLSTATE[mode]}'; END IF; RETURN {row}; END $$"
                )
            )
            await conn.execute(
                text(
                    f"CREATE CONSTRAINT TRIGGER {name} AFTER {event} ON {table} "
                    f"DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {name}()"
                )
            )

    async def disarm(self) -> None:
        async with self._engine.begin() as conn:
            for name, table in self._installed:
                await conn.execute(text(f"DROP TRIGGER IF EXISTS {name} ON {table}"))
                await conn.execute(text(f"DROP FUNCTION IF EXISTS {name}()"))
        self._installed.clear()


@pytest.fixture
async def fail_commit(app: FastAPI) -> AsyncIterator[CommitFailure]:
    """Arm COMMIT failures for chosen rows; every trigger is dropped when the test ends."""
    failure = CommitFailure(app.state.engine)
    try:
        yield failure
    finally:
        await failure.disarm()


# ---- seeding helpers ----


@pytest.fixture
def seed(app: FastAPI):  # type: ignore[no-untyped-def]
    """Return a helper object that inserts rows directly, bypassing the API, for arranging tests."""
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    class Seeder:
        async def user(
            self, *, role: str, email: str | None = None, password: str = "password123"
        ) -> uuid.UUID:
            from appointments_api.enums import UserRole
            from appointments_api.models import User

            async with maker() as session:
                user = User(
                    email=email or f"{role.lower()}-{uuid.uuid4().hex[:8]}@x.io",
                    full_name=f"{role} user",
                    role=UserRole(role),
                    hashed_password=hash_password(password),
                    is_active=True,
                )
                session.add(user)
                await session.commit()
                return user.id

        async def clinic(self, *, timezone: str = "UTC", cutoff_hours: int = 24) -> uuid.UUID:
            from appointments_api.models import Clinic

            async with maker() as session:
                clinic = Clinic(
                    name="Test Clinic",
                    timezone=timezone,
                    address="1 Test St",
                    cancellation_cutoff_hours=cutoff_hours,
                )
                session.add(clinic)
                await session.commit()
                return clinic.id

        async def clinician(
            self,
            *,
            clinic_id: uuid.UUID,
            user_id: uuid.UUID,
            buffer_minutes: int = 0,
            working_hours: list[tuple[int, str, str]] | None = None,
        ) -> uuid.UUID:
            from datetime import time

            from appointments_api.models import Clinician, ClinicianWorkingHours

            async with maker() as session:
                clinician = Clinician(
                    clinic_id=clinic_id,
                    user_id=user_id,
                    specialty="GP",
                    buffer_minutes=buffer_minutes,
                    working_hours=[
                        ClinicianWorkingHours(
                            weekday=wd,
                            start_time=time.fromisoformat(s),
                            end_time=time.fromisoformat(e),
                        )
                        for (wd, s, e) in (working_hours or [])
                    ],
                )
                session.add(clinician)
                await session.commit()
                return clinician.id

        async def service(self, *, clinic_id: uuid.UUID, duration_minutes: int = 30) -> uuid.UUID:
            from appointments_api.models import Service

            async with maker() as session:
                service = Service(
                    clinic_id=clinic_id,
                    name="Consult",
                    duration_minutes=duration_minutes,
                    price_cents=5000,
                    currency="USD",
                    is_active=True,
                )
                session.add(service)
                await session.commit()
                return service.id

        async def audit(
            self,
            *,
            action: str = "appointment.created",
            entity_type: str = "appointment",
            entity_id: str | None = None,
            actor_id: uuid.UUID | None = None,
            created_at: datetime | None = None,
        ) -> uuid.UUID:
            from appointments_api.models import AuditLogEntry

            async with maker() as session:
                entry = AuditLogEntry(
                    actor_id=actor_id,
                    action=action,
                    entity_type=entity_type,
                    entity_id=entity_id or uuid.uuid4().hex,
                    before=None,
                    after={"status": "REQUESTED"},
                )
                if created_at is not None:
                    entry.created_at = created_at
                session.add(entry)
                await session.commit()
                return entry.id

    return Seeder()


@pytest.fixture
def login(client: httpx.AsyncClient):  # type: ignore[no-untyped-def]
    """Return an async ``login(email, password)`` that yields an access token."""

    async def _login(email: str, password: str = "password123") -> str:
        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        response.raise_for_status()
        token: str = response.json()["access_token"]
        return token

    return _login


@pytest.fixture
async def booking_env(seed, login):  # type: ignore[no-untyped-def]
    """A ready-to-book world: one clinic (open all week), one clinician, one service, and a
    patient + admin token. Shared by the milestone-4 idempotency and ETag suites."""
    all_week = [(wd, "06:00", "22:00") for wd in range(7)]
    await seed.user(role="PLATFORM_ADMIN", email="admin@x.io")
    patient_id = await seed.user(role="PATIENT", email="patient@x.io")
    clinician_user = await seed.user(role="CLINICIAN", email="doc@x.io")
    clinic_id = await seed.clinic(timezone="UTC", cutoff_hours=24)
    clinician_id = await seed.clinician(
        clinic_id=clinic_id, user_id=clinician_user, working_hours=all_week
    )
    service_id = await seed.service(clinic_id=clinic_id, duration_minutes=30)
    return {
        "patient_id": patient_id,
        "clinic_id": clinic_id,
        "clinician_id": clinician_id,
        "service_id": service_id,
        "patient_token": await login("patient@x.io"),
        "admin_token": await login("admin@x.io"),
    }
