"""Authorization matrix: every role against every guarded endpoint.

Two laws are asserted for the whole surface at once:

* **Authentication** — a guarded endpoint with no token answers 401, never leaks data or acts.
* **Authorization** — a role that is not permitted answers 403; a permitted role gets past the guard
  (it may then hit 422 validation or 404, but never 401/403).

The matrix is data-driven so adding an endpoint is one row, and a new role automatically fans out
across every row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from tests.integration.helpers import auth_header
from tests.security.conftest import ALL_ROLES

_ALL = frozenset(ALL_ROLES)
_ADMIN = frozenset({"PLATFORM_ADMIN"})
_CLINIC_STAFF = frozenset({"CLINIC_ADMIN", "PLATFORM_ADMIN"})
# Telemetry reads and excursion acknowledgement are for clinical staff, not patients.
_STAFF = frozenset({"CLINICIAN", "CLINIC_ADMIN", "PLATFORM_ADMIN"})
_RANDOM_ID = "00000000-0000-4000-8000-000000000000"


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    allowed: frozenset[str]
    params: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] | None = None

    def __str__(self) -> str:  # readable test ids
        return f"{self.method} {self.path}"


# Role-gated writes: wrong role must 403 *before* any body validation, so an empty body is fine.
# Any-authenticated reads: allowed = every role; required query params are supplied so the only
# possible guard failure is authentication/authorization, not a 422 for a missing parameter.
ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint("POST", "/api/v1/users", _ADMIN, body={}),
    Endpoint("POST", "/api/v1/clinics", _ADMIN, body={}),
    Endpoint("POST", "/api/v1/clinicians", _CLINIC_STAFF, body={}),
    Endpoint("POST", "/api/v1/services", _CLINIC_STAFF, body={}),
    Endpoint("POST", "/api/v1/webhooks/subscriptions", _CLINIC_STAFF, body={}),
    Endpoint("GET", "/api/v1/webhooks/subscriptions", _CLINIC_STAFF),
    Endpoint("DELETE", f"/api/v1/webhooks/subscriptions/{_RANDOM_ID}", _CLINIC_STAFF),
    Endpoint("GET", "/api/v1/auth/me", _ALL),
    Endpoint("GET", "/api/v1/clinics", _ALL),
    Endpoint("GET", f"/api/v1/clinics/{_RANDOM_ID}", _ALL),
    Endpoint("GET", "/api/v1/clinicians", _ALL, params={"clinic_id": _RANDOM_ID}),
    Endpoint("GET", "/api/v1/services", _ALL, params={"clinic_id": _RANDOM_ID}),
    Endpoint("GET", "/api/v1/appointments", _ALL),
    Endpoint(
        "GET",
        "/api/v1/availability",
        _ALL,
        params={"clinician_id": _RANDOM_ID, "service_id": _RANDOM_ID, "day": "2030-01-01"},
    ),
    # Telemetry (milestone 10). The batch-ingest and SSE endpoints are excluded here: the batch
    # endpoint authenticates with a per-device secret (its own auth test lives in the integration
    # tier), and the SSE stream cannot be called synchronously in a matrix (unbounded response).
    Endpoint("POST", "/api/v1/devices", _CLINIC_STAFF, body={}),
    Endpoint("GET", "/api/v1/devices", _STAFF),
    Endpoint("GET", f"/api/v1/devices/{_RANDOM_ID}", _STAFF),
    Endpoint("POST", f"/api/v1/devices/{_RANDOM_ID}/credentials:rotate", _CLINIC_STAFF),
    Endpoint("GET", f"/api/v1/devices/{_RANDOM_ID}/health", _STAFF),
    Endpoint("GET", f"/api/v1/devices/{_RANDOM_ID}/telemetry", _STAFF),
    Endpoint("GET", f"/api/v1/devices/{_RANDOM_ID}/excursions", _STAFF),
    Endpoint("POST", "/api/v1/threshold-policies", _CLINIC_STAFF, body={}),
    Endpoint("GET", f"/api/v1/excursions/{_RANDOM_ID}", _STAFF),
    Endpoint("POST", f"/api/v1/excursions/{_RANDOM_ID}:acknowledge", _STAFF),
)


async def _send(
    client: httpx.AsyncClient, ep: Endpoint, headers: dict[str, str] | None = None
) -> httpx.Response:
    return await client.request(
        ep.method, ep.path, params=ep.params or None, json=ep.body, headers=headers
    )


@pytest.mark.security
@pytest.mark.parametrize("ep", ENDPOINTS, ids=str)
async def test_guarded_endpoint_requires_authentication(
    client: httpx.AsyncClient, ep: Endpoint
) -> None:
    response = await _send(client, ep)
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.security
@pytest.mark.parametrize("role", ALL_ROLES)
@pytest.mark.parametrize("ep", ENDPOINTS, ids=str)
async def test_role_is_enforced(
    client: httpx.AsyncClient, role_tokens: dict[str, str], ep: Endpoint, role: str
) -> None:
    response = await _send(client, ep, headers=auth_header(role_tokens[role]))
    if role in ep.allowed:
        # Past the guard: anything but the auth/authorization rejections.
        assert response.status_code not in (401, 403), response.text
    else:
        assert response.status_code == 403, response.text
        assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.security
async def test_a_bad_bearer_token_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


@pytest.mark.security
async def test_unknown_uuid_path_is_not_a_server_error(
    client: httpx.AsyncClient, role_tokens: dict[str, str]
) -> None:
    # A syntactically valid but unknown id is a clean 404, never a crash.
    response = await client.get(
        f"/api/v1/clinics/{uuid.uuid4()}", headers=auth_header(role_tokens["PLATFORM_ADMIN"])
    )
    assert response.status_code == 404
