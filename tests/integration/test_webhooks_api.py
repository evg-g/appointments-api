"""Webhook subscription API: create, list, delete, and delete-not-found.

The create/list authorization is covered by the security authz matrix; here we exercise the delete
branches (204 success and 404) that the earlier suites did not reach.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from tests.integration.helpers import auth_header


@pytest.fixture
async def admin(seed: Any, login: Any) -> str:
    await seed.user(role="PLATFORM_ADMIN", email="admin@wh.io")
    token: str = await login("admin@wh.io")
    return token


_SUBSCRIPTION = {
    "url": "https://example.test/hook",
    "secret": "0123456789abcdef",
    "event_types": ["appointment.created"],
    "clinic_id": None,
}


async def test_create_list_delete(client: httpx.AsyncClient, admin: str) -> None:
    created = await client.post(
        "/api/v1/webhooks/subscriptions", json=_SUBSCRIPTION, headers=auth_header(admin)
    )
    assert created.status_code == 201, created.text
    subscription_id = created.json()["id"]

    listing = await client.get("/api/v1/webhooks/subscriptions", headers=auth_header(admin))
    assert listing.status_code == 200
    assert any(row["id"] == subscription_id for row in listing.json())

    deleted = await client.delete(
        f"/api/v1/webhooks/subscriptions/{subscription_id}", headers=auth_header(admin)
    )
    assert deleted.status_code == 204


async def test_delete_unknown_subscription_is_404(client: httpx.AsyncClient, admin: str) -> None:
    response = await client.delete(
        f"/api/v1/webhooks/subscriptions/{uuid.uuid4()}", headers=auth_header(admin)
    )
    assert response.status_code == 404
