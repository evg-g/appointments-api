"""Authorization matrix: 401 without a token, 403 for the wrong role, happy path for the right."""

from __future__ import annotations

from typing import Any

import httpx

from tests.integration.helpers import auth_header

_CLINIC_BODY = {"name": "C", "timezone": "UTC", "address": "1 St"}


async def test_creating_a_clinic_requires_a_token(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/clinics", json=_CLINIC_BODY)
    assert response.status_code == 401


async def test_patient_cannot_create_a_clinic(client: httpx.AsyncClient, seed, login) -> None:  # type: ignore[no-untyped-def]
    await seed.user(role="PATIENT", email="patient@x.io")
    token = await login("patient@x.io")
    response = await client.post("/api/v1/clinics", json=_CLINIC_BODY, headers=auth_header(token))
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_platform_admin_can_create_a_clinic(client: httpx.AsyncClient, seed, login) -> None:  # type: ignore[no-untyped-def]
    await seed.user(role="PLATFORM_ADMIN", email="admin@x.io")
    token = await login("admin@x.io")
    response = await client.post("/api/v1/clinics", json=_CLINIC_BODY, headers=auth_header(token))
    assert response.status_code == 201
    assert response.json()["name"] == "C"


async def test_unknown_timezone_is_a_422_validation_problem(
    client: httpx.AsyncClient, seed: Any, login: Any
) -> None:
    await seed.user(role="PLATFORM_ADMIN", email="admin@x.io")
    token = await login("admin@x.io")
    body = {"name": "C", "timezone": "Mars/Phobos", "address": "1 St"}
    response = await client.post("/api/v1/clinics", json=body, headers=auth_header(token))
    assert response.status_code == 422
    assert response.json()["type"].endswith("/validation-error")
    assert response.json()["errors"]
