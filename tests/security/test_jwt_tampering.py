"""JWT tampering: the access-token guard must reject anything it did not itself issue.

Covers a forged signature, an ``alg: none`` token, a token signed with the wrong secret, an expired
token, and a refresh-typed token replayed as an access token. The final test pins the most important
property: even a *validly signed* token whose ``role`` claim has been inflated cannot escalate,
because authorization reads the role from the database, never from the token.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest

from appointments_api.config import get_settings
from appointments_api.enums import UserRole
from appointments_api.security import create_access_token
from tests.integration.helpers import auth_header

pytestmark = pytest.mark.security

# The app signs and verifies access tokens with the process-wide ``get_settings()`` (not the
# container ``settings`` fixture, whose JWT secret differs). Tests that need a *validly signed*
# token — so the specific rejection reason is what's under test — must use this same secret.


def _claims(sub: str, *, role: str = "PATIENT", token_type: str = "access") -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "sub": sub,
        "role": role,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }


def _b64(data: dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unsigned_token(claims: dict[str, Any]) -> str:
    """An ``alg: none`` token with an empty signature, as an attacker would forge one."""
    return f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(claims)}."


async def test_a_forged_signature_is_rejected(
    client: httpx.AsyncClient, seed: Any, login: Any
) -> None:
    await seed.user(role="PATIENT", email="p@x.io")
    token = await login("p@x.io")
    header, payload, signature = token.split(".")
    # Flip the signature to something structurally valid but wrong.
    forged = f"{header}.{payload}.{signature[:-4]}AAAA"
    response = await client.get("/api/v1/auth/me", headers=auth_header(forged))
    assert response.status_code == 401


async def test_alg_none_token_is_rejected(client: httpx.AsyncClient) -> None:
    token = _unsigned_token(_claims(str(uuid.uuid4())))
    response = await client.get("/api/v1/auth/me", headers=auth_header(token))
    assert response.status_code == 401


async def test_token_signed_with_the_wrong_secret_is_rejected(
    client: httpx.AsyncClient,
) -> None:
    token = jwt.encode(_claims(str(uuid.uuid4())), "attacker-secret", algorithm="HS256")
    response = await client.get("/api/v1/auth/me", headers=auth_header(token))
    assert response.status_code == 401


async def test_expired_token_is_rejected(client: httpx.AsyncClient, seed: Any) -> None:
    settings = get_settings()
    user_id = await seed.user(role="PATIENT", email="expired@x.io")
    past = datetime.now(UTC) - timedelta(hours=1)
    claims = {
        "sub": str(user_id),
        "role": "PATIENT",
        "type": "access",
        "iat": int((past - timedelta(minutes=15)).timestamp()),
        "exp": int(past.timestamp()),
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    response = await client.get("/api/v1/auth/me", headers=auth_header(token))
    assert response.status_code == 401


async def test_refresh_typed_token_cannot_be_used_as_access(
    client: httpx.AsyncClient, seed: Any
) -> None:
    settings = get_settings()
    user_id = await seed.user(role="PATIENT", email="refresh@x.io")
    token = jwt.encode(
        _claims(str(user_id), token_type="refresh"),
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    response = await client.get("/api/v1/auth/me", headers=auth_header(token))
    assert response.status_code == 401


async def test_inflated_role_claim_does_not_escalate(client: httpx.AsyncClient, seed: Any) -> None:
    # A patient forges nothing about the signature — the token is validly signed by us — but claims
    # to be a platform admin. Authorization must still read the real role from the database.
    patient_id = await seed.user(role="PATIENT", email="sneaky@x.io")
    token = create_access_token(user_id=patient_id, role=UserRole.PLATFORM_ADMIN)
    response = await client.post(
        "/api/v1/clinics",
        json={"name": "C", "timezone": "UTC", "address": "1 St"},
        headers=auth_header(token),
    )
    assert response.status_code == 403
