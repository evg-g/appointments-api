"""Cursor pagination stays stable when rows are inserted mid-listing."""

from __future__ import annotations

from typing import Any

import httpx

from tests.integration.helpers import auth_header


async def _create_clinic(client: httpx.AsyncClient, headers: dict[str, str], name: str) -> str:
    response = await client.post(
        "/api/v1/clinics",
        json={"name": name, "timezone": "UTC", "address": "1 St"},
        headers=headers,
    )
    clinic_id: str = response.json()["id"]
    return clinic_id


async def test_cursor_pagination_is_stable_under_inserts(
    client: httpx.AsyncClient, seed: Any, login: Any
) -> None:
    await seed.user(role="PLATFORM_ADMIN", email="admin@x.io")
    headers = auth_header(await login("admin@x.io"))

    original_ids = [await _create_clinic(client, headers, f"C{i}") for i in range(5)]

    # First page (2 newest).
    resp1 = await client.get("/api/v1/clinics", params={"limit": 2}, headers=headers)
    page1 = resp1.json()
    assert resp1.status_code == 200, (resp1.status_code, page1)
    assert page1["page"]["has_more"] is True
    seen = [c["id"] for c in page1["data"]]
    cursor = page1["page"]["next_cursor"]

    # Insert a brand-new clinic *after* page 1 was read.
    new_id = await _create_clinic(client, headers, "C-inserted")

    # Continue paging with the cursor.
    while cursor:
        params = {"limit": 2, "cursor": cursor}
        body = (await client.get("/api/v1/clinics", params=params, headers=headers)).json()
        seen.extend(c["id"] for c in body["data"])
        cursor = body["page"]["next_cursor"] if body["page"]["has_more"] else None

    # Every original appears exactly once; nothing is duplicated or skipped...
    assert len(seen) == len(set(seen))
    assert set(seen) == set(original_ids)
    # ...and the row inserted mid-pagination does not sneak into a later page.
    assert new_id not in seen
