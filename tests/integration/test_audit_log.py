"""The admin-only audit-log read endpoint: RBAC, keyset pagination, and filters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from tests.integration.helpers import auth_header


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/audit-log")
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_forbidden_for_non_admin(client: httpx.AsyncClient, seed: Any, login: Any) -> None:
    await seed.user(role="PATIENT", email="patient@x.io")
    resp = await client.get("/api/v1/audit-log", headers=auth_header(await login("patient@x.io")))
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_admin_lists_entries_newest_first(
    client: httpx.AsyncClient, seed: Any, login: Any
) -> None:
    await seed.user(role="PLATFORM_ADMIN", email="admin@x.io")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(3):
        await seed.audit(action="appointment.created", created_at=base + timedelta(minutes=i))

    resp = await client.get("/api/v1/audit-log", headers=auth_header(await login("admin@x.io")))
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert len(body["data"]) == 3
    # Newest first.
    timestamps = [row["created_at"] for row in body["data"]]
    assert timestamps == sorted(timestamps, reverse=True)


async def test_pagination_walks_all_rows_once(
    client: httpx.AsyncClient, seed: Any, login: Any
) -> None:
    await seed.user(role="PLATFORM_ADMIN", email="admin@x.io")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        await seed.audit(created_at=base + timedelta(minutes=i))
    headers = auth_header(await login("admin@x.io"))

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        params: dict[str, Any] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        body = (await client.get("/api/v1/audit-log", params=params, headers=headers)).json()
        seen.extend(row["id"] for row in body["data"])
        if not body["page"]["has_more"]:
            break
        cursor = body["page"]["next_cursor"]

    assert len(seen) == 5
    assert len(set(seen)) == 5


async def test_filters_by_action_and_entity_type(
    client: httpx.AsyncClient, seed: Any, login: Any
) -> None:
    await seed.user(role="PLATFORM_ADMIN", email="admin@x.io")
    await seed.audit(action="appointment.created", entity_type="appointment")
    await seed.audit(action="appointment.cancelled", entity_type="appointment")
    await seed.audit(action="device.provisioned", entity_type="device")
    headers = auth_header(await login("admin@x.io"))

    resp = await client.get(
        "/api/v1/audit-log", params={"action": "appointment.cancelled"}, headers=headers
    )
    body = resp.json()
    assert resp.status_code == 200
    assert len(body["data"]) == 1
    assert body["data"][0]["action"] == "appointment.cancelled"

    resp2 = await client.get("/api/v1/audit-log", params={"entity_type": "device"}, headers=headers)
    body2 = resp2.json()
    assert len(body2["data"]) == 1
    assert body2["data"][0]["entity_type"] == "device"


async def test_malformed_cursor_is_a_validation_error(
    client: httpx.AsyncClient, seed: Any, login: Any
) -> None:
    await seed.user(role="PLATFORM_ADMIN", email="admin@x.io")
    resp = await client.get(
        "/api/v1/audit-log",
        params={"cursor": "not-a-cursor"},
        headers=auth_header(await login("admin@x.io")),
    )
    assert resp.status_code == 422
