"""Unit test for the liveness endpoint.

Liveness needs no dependencies, so it stays a unit test. Readiness now checks the database and
Redis, so it is covered in the integration tier (tests/integration/test_health.py).
"""

from __future__ import annotations

import httpx
import pytest

from appointments_api import __version__
from appointments_api.main import create_app


@pytest.fixture
def client() -> httpx.AsyncClient:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_liveness_reports_ok_and_version(client: httpx.AsyncClient) -> None:
    async with client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
