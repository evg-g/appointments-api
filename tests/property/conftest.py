"""Fixtures for the property-based tier.

Two very different needs live here:

* The pure ``hypothesis`` slot tests need nothing — they call functions directly.
* The Schemathesis tests need a *running* app on real Postgres + Redis. We reuse the integration
  containers (``settings``, ``_migrate``) for the backing services, but we cannot reuse the
  integration ``app`` fixture: Schemathesis drives each request through ``starlette``'s
  ``TestClient``, which opens and closes the app's lifespan **per call**. The production lifespan
  disposes the engine and Redis client on shutdown, so the second call would hit disposed
  resources. Here we give the fuzzed app a lifespan that *creates and disposes* its resources each
  cycle, so every call gets a fresh engine/Redis bound to that call's own event loop.

The admin user is seeded through a synchronous SQLAlchemy session so it is independent of any event
loop, then we mint a JWT for it directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import schemathesis
from fastapi import FastAPI
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import text
from sqlalchemy.orm import Session

from appointments_api.config import Settings
from appointments_api.db import create_engine, create_redis, create_sessionmaker
from appointments_api.enums import UserRole
from appointments_api.main import create_app
from appointments_api.models import User
from appointments_api.security import create_access_token, hash_password
from tests.integration.conftest import *  # noqa: F403  (shared container/settings fixtures)

# ``import *`` skips underscore-prefixed names, but the autouse migration fixture and the table
# list are both underscore-prefixed, so pull them in explicitly.
from tests.integration.conftest import (
    _TABLES,
    _migrate,  # noqa: F401
)


def _per_call_resource_lifespan(
    settings: Settings,
) -> Any:
    """A lifespan that (re)builds the engine/Redis on startup and disposes them on shutdown.

    Schemathesis runs the lifespan once per request, so this hands each request a fresh engine and
    Redis client bound to the loop that request runs on — the only reliable way to fuzz an async
    app through the synchronous ``TestClient`` without cross-event-loop errors.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_engine(settings)
        app.state.sessionmaker = create_sessionmaker(app.state.engine)
        app.state.redis = create_redis(settings)
        try:
            yield
        finally:
            await app.state.engine.dispose()
            await app.state.redis.aclose()

    return lifespan


@pytest.fixture
def admin_token(settings: Settings) -> str:
    """Truncate the database, seed one PLATFORM_ADMIN synchronously, and return its access token."""
    engine = create_sync_engine(settings.database_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
        with Session(engine) as session:
            admin = User(
                email="schemathesis-admin@x.io",
                full_name="Schemathesis Admin",
                role=UserRole.PLATFORM_ADMIN,
                hashed_password=hash_password("password123"),
                is_active=True,
            )
            session.add(admin)
            session.commit()
            admin_id = admin.id
    finally:
        engine.dispose()
    return create_access_token(user_id=admin_id, role=UserRole.PLATFORM_ADMIN, settings=settings)


@pytest.fixture
def api_schema(settings: Settings, admin_token: str) -> Any:
    """The OpenAPI schema Schemathesis fuzzes, served by an app with per-call resources."""
    app = create_app(settings)
    app.router.lifespan_context = _per_call_resource_lifespan(settings)
    return schemathesis.openapi.from_asgi("/openapi.json", app)
