"""Injection-shaped inputs: SQL metacharacters must be data, never code.

The API uses SQLAlchemy with bound parameters throughout, so these tests assert the observable
consequences: injection payloads are either rejected by validation or stored and echoed back
*verbatim*, the database is never harmed, and malformed identifiers give a clean 4xx, never a 500.
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration.helpers import auth_header

pytestmark = pytest.mark.security

# Classic SQL-injection shapes plus an encoded one.
_SQLI_PAYLOADS = [
    "'; DROP TABLE clinics; --",
    "' OR '1'='1",
    '" OR ""="',
    "1); DELETE FROM users; --",
    "admin'--",
]


@pytest.mark.parametrize("payload", _SQLI_PAYLOADS)
async def test_injection_in_login_email_never_500s(client: httpx.AsyncClient, payload: str) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": payload, "password": payload})
    # Either invalid-email validation (422) or bad credentials (401) — never a crash.
    assert response.status_code in (401, 422)


@pytest.mark.parametrize("payload", _SQLI_PAYLOADS)
async def test_injection_in_a_stored_field_is_kept_literal(
    client: httpx.AsyncClient, role_tokens: dict[str, str], payload: str
) -> None:
    headers = auth_header(role_tokens["PLATFORM_ADMIN"])
    create = await client.post(
        "/api/v1/clinics",
        json={"name": payload, "timezone": "UTC", "address": payload},
        headers=headers,
    )
    assert create.status_code == 201
    clinic_id = create.json()["id"]

    # Read it back: the payload must round-trip byte-for-byte (proof it was bound, not interpreted).
    fetched = await client.get(f"/api/v1/clinics/{clinic_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == payload

    # And the table it tried to drop is still there and queryable.
    listing = await client.get("/api/v1/clinics", headers=headers)
    assert listing.status_code == 200
    assert any(row["id"] == clinic_id for row in listing.json()["data"])


@pytest.mark.parametrize("payload", _SQLI_PAYLOADS)
async def test_injection_in_a_uuid_path_is_a_clean_422(
    client: httpx.AsyncClient, role_tokens: dict[str, str], payload: str
) -> None:
    response = await client.get(
        f"/api/v1/clinics/{payload}", headers=auth_header(role_tokens["PATIENT"])
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize("payload", _SQLI_PAYLOADS)
async def test_injection_in_a_uuid_query_is_a_clean_422(
    client: httpx.AsyncClient, role_tokens: dict[str, str], payload: str
) -> None:
    response = await client.get(
        "/api/v1/clinicians",
        params={"clinic_id": payload},
        headers=auth_header(role_tokens["PATIENT"]),
    )
    assert response.status_code == 422


async def test_injection_in_the_cursor_is_a_clean_422(
    client: httpx.AsyncClient, role_tokens: dict[str, str]
) -> None:
    response = await client.get(
        "/api/v1/clinics",
        params={"cursor": "'; DROP TABLE clinics; --"},
        headers=auth_header(role_tokens["PATIENT"]),
    )
    assert response.status_code == 422
