"""Per-principal rate limiting end to end against the real Redis container (spec §5).

Builds a dedicated app with a low limit so the block is reached in a few calls, then confirms the
429 + Retry-After + RateLimit-* headers, and that allowed responses carry the headers too.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text

from appointments_api.config import Settings
from appointments_api.enums import UserRole
from appointments_api.main import create_app
from appointments_api.models import User
from appointments_api.security import hash_password
from tests.integration.conftest import _TABLES
from tests.integration.helpers import auth_header


@pytest.fixture
async def low_limit_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    low = settings.model_copy(
        update={
            "rate_limit_enabled": True,
            "rate_limit_requests": 3,
            "rate_limit_window_seconds": 60,
        }
    )
    app = create_app(low)
    engine = app.state.engine
    redis = app.state.redis
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
    await redis.flushdb()
    # Seed one patient directly so we can obtain a token.
    async with app.state.sessionmaker() as session:
        session.add(
            User(
                email="rl@x.io",
                full_name="Rate Limited",
                role=UserRole.PATIENT,
                hashed_password=hash_password("password123"),
                is_active=True,
            )
        )
        await session.commit()
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        await engine.dispose()
        await redis.aclose()


async def test_rate_limit_blocks_after_the_limit_with_headers(
    low_limit_client: httpx.AsyncClient,
) -> None:
    login = await low_limit_client.post(
        "/api/v1/auth/login", json={"email": "rl@x.io", "password": "password123"}
    )
    headers = auth_header(login.json()["access_token"])

    # limit=3: three /auth/me calls (keyed by the token subject) are allowed, the fourth is blocked.
    first = await low_limit_client.get("/api/v1/auth/me", headers=headers)
    assert first.status_code == 200
    assert first.headers["RateLimit-Limit"] == "3"
    assert first.headers["RateLimit-Remaining"] == "2"

    second = await low_limit_client.get("/api/v1/auth/me", headers=headers)
    third = await low_limit_client.get("/api/v1/auth/me", headers=headers)
    assert (second.status_code, third.status_code) == (200, 200)
    assert third.headers["RateLimit-Remaining"] == "0"

    blocked = await low_limit_client.get("/api/v1/auth/me", headers=headers)
    assert blocked.status_code == 429
    assert blocked.json()["type"].endswith("/rate-limited")
    assert int(blocked.headers["Retry-After"]) > 0
    assert blocked.headers["RateLimit-Remaining"] == "0"


async def test_health_endpoint_is_exempt(low_limit_client: httpx.AsyncClient) -> None:
    # Probes must never be throttled: many liveness calls stay 200 with no RateLimit headers.
    for _ in range(5):
        response = await low_limit_client.get("/health/live")
        assert response.status_code == 200
    assert "RateLimit-Limit" not in response.headers
