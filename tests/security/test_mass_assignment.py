"""Mass assignment: a client must not be able to set server-controlled fields by smuggling them
into a request body.

The input schemas are the whitelist — Pydantic drops unknown keys before the handler sees them — so
these tests confirm that smuggled ``id``/``status``/``version``/``is_active`` values are ignored and
the server's own values win. The patient-id spoof test confirms per-object ownership, which a schema
cannot enforce on its own.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tests.integration.helpers import auth_header

pytestmark = pytest.mark.security


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


async def test_smuggled_appointment_fields_are_ignored(
    client: httpx.AsyncClient, booking_env: dict[str, Any]
) -> None:
    starts_at = "2035-01-10T10:00:00+00:00"
    body = {
        "clinic_id": str(booking_env["clinic_id"]),
        "clinician_id": str(booking_env["clinician_id"]),
        "service_id": str(booking_env["service_id"]),
        "patient_id": str(booking_env["patient_id"]),
        "starts_at": starts_at,
        # --- all of the following are server-controlled and must be ignored ---
        "id": "11111111-1111-4111-8111-111111111111",
        "status": "CONFIRMED",
        "ends_at": "2035-01-10T23:59:00+00:00",
        "version": 999,
        "cancellation_reason": "smuggled",
    }
    response = await client.post(
        "/api/v1/appointments",
        json=body,
        headers=auth_header(booking_env["admin_token"]),
    )
    assert response.status_code == 201, response.text
    created = response.json()

    assert created["id"] != body["id"]  # server minted its own id
    assert created["status"] == "REQUESTED"  # not the smuggled CONFIRMED
    # ends_at is derived from the service duration (30 min), not the smuggled value.
    assert _parse(created["ends_at"]) == _parse(starts_at) + timedelta(minutes=30)


async def test_new_user_cannot_be_created_pre_deactivated(
    client: httpx.AsyncClient, seed: Any, login: Any
) -> None:
    await seed.user(role="PLATFORM_ADMIN", email="admin@ma.io")
    admin_token = await login("admin@ma.io")
    response = await client.post(
        "/api/v1/users",
        json={
            "email": "victim@ma.io",
            "full_name": "Victim",
            "role": "PATIENT",
            "password": "password123",
            # smuggled server-controlled fields
            "id": "22222222-2222-4222-8222-222222222222",
            "is_active": False,
        },
        headers=auth_header(admin_token),
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["id"] != "22222222-2222-4222-8222-222222222222"
    assert created["is_active"] is True


async def test_smuggled_clinic_id_is_ignored(
    client: httpx.AsyncClient, seed: Any, login: Any
) -> None:
    await seed.user(role="PLATFORM_ADMIN", email="admin2@ma.io")
    admin_token = await login("admin2@ma.io")
    smuggled = "33333333-3333-4333-8333-333333333333"
    response = await client.post(
        "/api/v1/clinics",
        json={"name": "C", "timezone": "UTC", "address": "1 St", "id": smuggled},
        headers=auth_header(admin_token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["id"] != smuggled


async def test_patient_cannot_book_on_behalf_of_another_patient(
    client: httpx.AsyncClient, booking_env: dict[str, Any], seed: Any, login: Any
) -> None:
    # A second patient the booking patient will try to impersonate.
    other_id = await seed.user(role="PATIENT", email="other-patient@ma.io")
    starts_at = datetime(2035, 2, 1, 10, 0, tzinfo=UTC).isoformat()
    response = await client.post(
        "/api/v1/appointments",
        json={
            "clinic_id": str(booking_env["clinic_id"]),
            "clinician_id": str(booking_env["clinician_id"]),
            "service_id": str(booking_env["service_id"]),
            "patient_id": str(other_id),  # spoof: not the caller
            "starts_at": starts_at,
        },
        headers=auth_header(booking_env["patient_token"]),
    )
    assert response.status_code == 403, response.text
