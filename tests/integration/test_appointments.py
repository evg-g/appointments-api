"""Appointment behaviours end to end: booking rules, the DB double-booking guard, the status
state machine, the cancellation window, and role-scoped listing."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from appointments_api.api.deps import get_clock
from tests.fakes.clock import FixedClock
from tests.integration.helpers import auth_header

ALL_WEEK = [(wd, "06:00", "22:00") for wd in range(7)]


@pytest.fixture
async def env(seed, login) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    await seed.user(role="PLATFORM_ADMIN", email="admin@x.io")
    patient_id = await seed.user(role="PATIENT", email="patient@x.io")
    clinician_user = await seed.user(role="CLINICIAN", email="doc@x.io")
    clinic_id = await seed.clinic(timezone="UTC", cutoff_hours=24)
    clinician_id = await seed.clinician(
        clinic_id=clinic_id, user_id=clinician_user, working_hours=ALL_WEEK
    )
    service_id = await seed.service(clinic_id=clinic_id, duration_minutes=30)
    return {
        "patient_id": patient_id,
        "clinic_id": clinic_id,
        "clinician_id": clinician_id,
        "service_id": service_id,
        "patient_token": await login("patient@x.io"),
        "admin_token": await login("admin@x.io"),
        "doc_token": await login("doc@x.io"),
    }


def _body(env: dict[str, Any], starts_at: str, patient_id: object | None = None) -> dict[str, Any]:
    body = {
        "clinic_id": str(env["clinic_id"]),
        "clinician_id": str(env["clinician_id"]),
        "service_id": str(env["service_id"]),
        "starts_at": starts_at,
    }
    if patient_id is not None:
        body["patient_id"] = str(patient_id)
    return body


async def test_patient_can_book_within_working_hours(
    client: httpx.AsyncClient, env: dict[str, Any]
) -> None:
    response = await client.post(
        "/api/v1/appointments",
        json=_body(env, "2027-06-01T10:00:00+00:00"),
        headers=auth_header(env["patient_token"]),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "REQUESTED"
    assert body["starts_at"] == "2027-06-01T10:00:00Z"
    assert body["ends_at"] == "2027-06-01T10:30:00Z"


async def test_booking_outside_working_hours_is_422(
    client: httpx.AsyncClient, env: dict[str, Any]
) -> None:
    response = await client.post(
        "/api/v1/appointments",
        json=_body(env, "2027-06-01T03:00:00+00:00"),  # before 06:00 window
        headers=auth_header(env["patient_token"]),
    )
    assert response.status_code == 422


async def test_overlapping_booking_is_rejected(
    client: httpx.AsyncClient, env: dict[str, Any]
) -> None:
    first = await client.post(
        "/api/v1/appointments",
        json=_body(env, "2027-06-01T10:00:00+00:00"),
        headers=auth_header(env["patient_token"]),
    )
    assert first.status_code == 201
    second = await client.post(
        "/api/v1/appointments",
        json=_body(env, "2027-06-01T10:15:00+00:00"),  # overlaps the first
        headers=auth_header(env["patient_token"]),
    )
    assert second.status_code == 409
    assert second.json()["type"].endswith("/slot-unavailable")


async def test_concurrent_double_booking_only_one_succeeds(
    client: httpx.AsyncClient, env: dict[str, Any]
) -> None:
    # Two identical bookings fired together. The DB exclusion constraint — not the app — is what
    # guarantees exactly one wins under a real race.
    body = _body(env, "2027-06-01T11:00:00+00:00")
    headers = auth_header(env["patient_token"])
    r1, r2 = await asyncio.gather(
        client.post("/api/v1/appointments", json=body, headers=headers),
        client.post("/api/v1/appointments", json=body, headers=headers),
    )
    assert sorted([r1.status_code, r2.status_code]) == [201, 409]


async def test_transition_confirm_then_illegal_transition(
    client: httpx.AsyncClient, env: dict[str, Any]
) -> None:
    created = await client.post(
        "/api/v1/appointments",
        json=_body(env, "2027-06-01T12:00:00+00:00"),
        headers=auth_header(env["patient_token"]),
    )
    appointment_id = created.json()["id"]
    created_etag = created.headers["ETag"]

    confirm = await client.post(
        f"/api/v1/appointments/{appointment_id}/transition",
        json={"target_status": "CONFIRMED"},
        headers={**auth_header(env["admin_token"]), "If-Match": created_etag},
    )
    assert confirm.status_code == 200
    assert confirm.json()["status"] == "CONFIRMED"
    # The successful write bumped the version, so the ETag must have moved on.
    confirm_etag = confirm.headers["ETag"]
    assert confirm_etag != created_etag

    illegal = await client.post(
        f"/api/v1/appointments/{appointment_id}/transition",
        json={"target_status": "REQUESTED"},
        headers={**auth_header(env["admin_token"]), "If-Match": confirm_etag},
    )
    assert illegal.status_code == 409


async def test_patient_cannot_transition_status(
    client: httpx.AsyncClient, env: dict[str, Any]
) -> None:
    created = await client.post(
        "/api/v1/appointments",
        json=_body(env, "2027-06-01T13:00:00+00:00"),
        headers=auth_header(env["patient_token"]),
    )
    appointment_id = created.json()["id"]
    response = await client.post(
        f"/api/v1/appointments/{appointment_id}/transition",
        json={"target_status": "CONFIRMED"},
        headers=auth_header(env["patient_token"]),
    )
    assert response.status_code == 403


async def test_cancellation_window_enforced_for_patient_but_not_admin(
    client: httpx.AsyncClient, app: Any, env: dict[str, Any]
) -> None:
    fixed_now = datetime(2027, 6, 1, 9, 0, tzinfo=UTC)
    app.dependency_overrides[get_clock] = lambda: FixedClock(fixed_now)

    # An appointment one hour away is inside the 24h cutoff.
    near = await client.post(
        "/api/v1/appointments",
        json=_body(env, "2027-06-01T10:00:00+00:00"),
        headers=auth_header(env["patient_token"]),
    )
    near_id = near.json()["id"]
    near_etag = near.headers["ETag"]
    # The window rule is authorization, checked before the ETag precondition, so this 403 needs
    # no If-Match.
    patient_cancel = await client.post(
        f"/api/v1/appointments/{near_id}/cancel",
        json={"reason": "cannot make it"},
        headers=auth_header(env["patient_token"]),
    )
    assert patient_cancel.status_code == 403

    # An admin may cancel inside the window.
    admin_cancel = await client.post(
        f"/api/v1/appointments/{near_id}/cancel",
        json={"reason": "clinic closed"},
        headers={**auth_header(env["admin_token"]), "If-Match": near_etag},
    )
    assert admin_cancel.status_code == 200
    assert admin_cancel.json()["status"] == "CANCELLED"

    # A far-off appointment is outside the cutoff, so the patient may cancel it.
    far = await client.post(
        "/api/v1/appointments",
        json=_body(env, "2027-06-05T10:00:00+00:00"),
        headers=auth_header(env["patient_token"]),
    )
    far_id = far.json()["id"]
    far_etag = far.headers["ETag"]
    far_cancel = await client.post(
        f"/api/v1/appointments/{far_id}/cancel",
        json={},
        headers={**auth_header(env["patient_token"]), "If-Match": far_etag},
    )
    assert far_cancel.status_code == 200


async def test_patient_sees_only_their_own_appointments(
    client: httpx.AsyncClient, seed: Any, env: dict[str, Any]
) -> None:
    # patient@x.io books one; admin books one for a second patient.
    other_patient = await seed.user(role="PATIENT", email="other@x.io")
    await client.post(
        "/api/v1/appointments",
        json=_body(env, "2027-06-01T14:00:00+00:00"),
        headers=auth_header(env["patient_token"]),
    )
    await client.post(
        "/api/v1/appointments",
        json=_body(env, "2027-06-01T15:00:00+00:00", patient_id=other_patient),
        headers=auth_header(env["admin_token"]),
    )

    mine = await client.get("/api/v1/appointments", headers=auth_header(env["patient_token"]))
    assert mine.status_code == 200, mine.json()
    data = mine.json()["data"]
    assert len(data) == 1, mine.json()
    assert data[0]["patient_id"] == str(env["patient_id"])

    all_seen = await client.get("/api/v1/appointments", headers=auth_header(env["admin_token"]))
    assert len(all_seen.json()["data"]) == 2
