"""Auth flows: login, refresh, and the current-user dependency.

Covers the login success/failure branches (bad credentials, inactive account), refresh rotation,
and the ``get_current_user`` guard that rejects a token whose subject is gone or inactive.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from fastapi import FastAPI

from appointments_api.enums import UserRole
from appointments_api.security import create_access_token, hash_password
from tests.integration.helpers import auth_header


async def _insert_user(app: FastAPI, *, email: str, is_active: bool) -> uuid.UUID:
    from appointments_api.models import User

    maker = app.state.sessionmaker
    async with maker() as session:
        user = User(
            email=email,
            full_name="Test",
            role=UserRole.PATIENT,
            hashed_password=hash_password("password123"),
            is_active=is_active,
        )
        session.add(user)
        await session.commit()
        user_id: uuid.UUID = user.id
        return user_id


async def test_login_success_returns_tokens(client: httpx.AsyncClient, seed: Any) -> None:
    await seed.user(role="PATIENT", email="login@x.io")
    response = await client.post(
        "/api/v1/auth/login", json={"email": "login@x.io", "password": "password123"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] > 0


async def test_login_with_wrong_password_is_401(client: httpx.AsyncClient, seed: Any) -> None:
    await seed.user(role="PATIENT", email="pw@x.io")
    response = await client.post(
        "/api/v1/auth/login", json={"email": "pw@x.io", "password": "wrong-password"}
    )
    assert response.status_code == 401


async def test_login_unknown_email_is_401(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@x.io", "password": "password123"}
    )
    assert response.status_code == 401


async def test_login_inactive_account_is_401(client: httpx.AsyncClient, app: FastAPI) -> None:
    await _insert_user(app, email="inactive@x.io", is_active=False)
    response = await client.post(
        "/api/v1/auth/login", json={"email": "inactive@x.io", "password": "password123"}
    )
    assert response.status_code == 401


async def test_refresh_rotates_the_token(client: httpx.AsyncClient, seed: Any) -> None:
    await seed.user(role="PATIENT", email="refresh@x.io")
    login = await client.post(
        "/api/v1/auth/login", json={"email": "refresh@x.io", "password": "password123"}
    )
    original_refresh = login.json()["refresh_token"]

    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": original_refresh})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["refresh_token"] != original_refresh


async def test_refresh_with_a_bogus_token_is_401(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "not-a-real-refresh-token"}
    )
    assert response.status_code == 401


async def test_token_for_a_missing_user_is_rejected(client: httpx.AsyncClient) -> None:
    # Valid signature, but the subject id does not exist in the database.
    token = create_access_token(user_id=uuid.uuid4(), role=UserRole.PATIENT)
    response = await client.get("/api/v1/auth/me", headers=auth_header(token))
    assert response.status_code == 401


async def test_token_for_an_inactive_user_is_rejected(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    user_id = await _insert_user(app, email="deactivated@x.io", is_active=False)
    token = create_access_token(user_id=user_id, role=UserRole.PATIENT)
    response = await client.get("/api/v1/auth/me", headers=auth_header(token))
    assert response.status_code == 401
