"""Auth flows: login, whoami, and refresh-token rotation with reuse detection."""

from __future__ import annotations

import httpx

from tests.integration.helpers import auth_header


async def test_login_success_returns_tokens(client: httpx.AsyncClient, seed) -> None:  # type: ignore[no-untyped-def]
    await seed.user(role="PATIENT", email="patient@x.io")
    response = await client.post(
        "/api/v1/auth/login", json={"email": "patient@x.io", "password": "password123"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"


async def test_login_with_wrong_password_is_401(client: httpx.AsyncClient, seed) -> None:  # type: ignore[no-untyped-def]
    await seed.user(role="PATIENT", email="patient@x.io")
    response = await client.post(
        "/api/v1/auth/login", json={"email": "patient@x.io", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_me_requires_a_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_me_returns_current_user(client: httpx.AsyncClient, seed, login) -> None:  # type: ignore[no-untyped-def]
    await seed.user(role="PATIENT", email="patient@x.io")
    token = await login("patient@x.io")
    response = await client.get("/api/v1/auth/me", headers=auth_header(token))
    assert response.status_code == 200
    assert response.json()["email"] == "patient@x.io"
    assert response.json()["role"] == "PATIENT"


async def test_refresh_rotates_and_detects_reuse(client: httpx.AsyncClient, seed) -> None:  # type: ignore[no-untyped-def]
    await seed.user(role="PATIENT", email="patient@x.io")
    login_resp = await client.post(
        "/api/v1/auth/login", json={"email": "patient@x.io", "password": "password123"}
    )
    refresh1 = login_resp.json()["refresh_token"]

    # First refresh: rotates to a new token.
    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh1})
    assert r2.status_code == 200
    refresh2 = r2.json()["refresh_token"]
    assert refresh2 != refresh1

    # Replaying the old (consumed) token is reuse -> 401 and the whole family is revoked.
    reused = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh1})
    assert reused.status_code == 401

    # refresh2 belonged to the revoked family, so it no longer works either.
    after_revoke = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh2})
    assert after_revoke.status_code == 401


async def test_logout_revokes_refresh_token(client: httpx.AsyncClient, seed) -> None:  # type: ignore[no-untyped-def]
    await seed.user(role="PATIENT", email="patient@x.io")
    login_resp = await client.post(
        "/api/v1/auth/login", json={"email": "patient@x.io", "password": "password123"}
    )
    refresh = login_resp.json()["refresh_token"]
    assert (
        await client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    ).status_code == 204
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    ).status_code == 401
