"""Unit tests for the health endpoints.

These use httpx's ASGI transport to call the app in-process — no network, no server, no
Docker. They prove the app wiring and response models are correct.
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
    # Arrange / Act
    async with client:
        response = await client.get("/health/live")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_readiness_reports_process_check(client: httpx.AsyncClient) -> None:
    async with client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["process"] == "ok"
