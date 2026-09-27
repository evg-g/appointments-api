"""Readiness against real Postgres + Redis."""

from __future__ import annotations

import httpx


async def test_readiness_reports_all_dependencies_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"process": "ok", "database": "ok", "redis": "ok"}
