"""End-to-end appointment flows: create, list, read, transition, cancel.

These drive the appointments router through its real branches — role scoping, working-hours and
overlap rejection, the ETag/If-Match preconditions, the state machine, and the cancellation window —
against real Postgres and Redis. They close the router coverage the earlier milestones left open.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tests.integration.helpers import auth_header

_RANDOM = str(uuid.uuid4())


def _at(hours_from_now: float) -> str:
    """A whole-hour ISO timestamp offset from now (clinic below is open 24/7, so any hour fits)."""
    moment = (datetime.now(UTC) + timedelta(hours=hours_from_now)).replace(
        minute=0, second=0, microsecond=0
    )
    return moment.isoformat()


@pytest.fixture
async def world(seed: Any, login: Any) -> dict[str, Any]:
    """One clinic open every day 00:00-23:59 (so time-of-day never flakes), a clinician, a service,
    a patient, and tokens for patient/clinician/admin."""
    hours = [(wd, "00:00", "23:59") for wd in range(7)]
    await seed.user(role="PLATFORM_ADMIN", email="admin@ap.io")
    patient_id = await seed.user(role="PATIENT", email="pat@ap.io")
    doc_user = await seed.user(role="CLINICIAN", email="doc@ap.io")
    clinic_id = await seed.clinic(timezone="UTC", cutoff_hours=24)
    clinician_id = await seed.clinician(clinic_id=clinic_id, user_id=doc_user, working_hours=hours)
    service_id = await seed.service(clinic_id=clinic_id, duration_minutes=30)
    return {
        "patient_id": patient_id,
        "clinic_id": clinic_id,
        "clinician_id": clinician_id,
        "service_id": service_id,
        "patient_token": await login("pat@ap.io"),
        "doc_token": await login("doc@ap.io"),
        "admin_token": await login("admin@ap.io"),
    }


def _body(
    world: dict[str, Any], *, starts_at: str, patient_id: str | None = None
) -> dict[str, Any]:
    body = {
        "clinic_id": str(world["clinic_id"]),
        "clinician_id": str(world["clinician_id"]),
        "service_id": str(world["service_id"]),
        "starts_at": starts_at,
    }
    if patient_id is not None:
        body["patient_id"] = patient_id
    return body


async def _book(
    client: httpx.AsyncClient, world: dict[str, Any], token: str, **kw: Any
) -> httpx.Response:
    return await client.post(
        "/api/v1/appointments", json=_body(world, **kw), headers=auth_header(token)
    )


# ---- create ----


async def test_patient_books_for_self(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    starts_at = _at(48)
    response = await _book(client, world, world["patient_token"], starts_at=starts_at)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "REQUESTED"
    assert body["patient_id"] == str(world["patient_id"])
    assert body["version"] == 1
    assert response.headers["etag"] == '"1"'
    ends = datetime.fromisoformat(body["ends_at"])
    assert ends - datetime.fromisoformat(body["starts_at"]) == timedelta(minutes=30)


async def test_staff_books_on_behalf(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    response = await _book(
        client, world, world["admin_token"], starts_at=_at(48), patient_id=str(world["patient_id"])
    )
    assert response.status_code == 201, response.text


async def test_staff_must_supply_patient_id(
    client: httpx.AsyncClient, world: dict[str, Any]
) -> None:
    response = await _book(client, world, world["admin_token"], starts_at=_at(48))
    assert response.status_code == 422


async def test_staff_patient_id_must_be_a_patient(
    client: httpx.AsyncClient, world: dict[str, Any]
) -> None:
    response = await _book(
        client, world, world["admin_token"], starts_at=_at(48), patient_id=_RANDOM
    )
    assert response.status_code == 422


async def test_unknown_clinic_clinician_service_are_404(
    client: httpx.AsyncClient, world: dict[str, Any]
) -> None:
    pid = str(world["patient_id"])
    token = world["admin_token"]
    bad_clinic = {**_body(world, starts_at=_at(48), patient_id=pid), "clinic_id": _RANDOM}
    bad_clinician = {**_body(world, starts_at=_at(48), patient_id=pid), "clinician_id": _RANDOM}
    bad_service = {**_body(world, starts_at=_at(48), patient_id=pid), "service_id": _RANDOM}
    for body in (bad_clinic, bad_clinician, bad_service):
        response = await client.post("/api/v1/appointments", json=body, headers=auth_header(token))
        assert response.status_code == 404, response.text


async def test_cross_clinic_service_is_422(
    client: httpx.AsyncClient, world: dict[str, Any], seed: Any
) -> None:
    other_clinic = await seed.clinic(timezone="UTC")
    other_service = await seed.service(clinic_id=other_clinic)
    body = {
        **_body(world, starts_at=_at(48), patient_id=str(world["patient_id"])),
        "service_id": str(other_service),
    }
    response = await client.post(
        "/api/v1/appointments", json=body, headers=auth_header(world["admin_token"])
    )
    assert response.status_code == 422


async def test_inactive_service_is_422(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    created = await client.post(
        "/api/v1/services",
        json={
            "clinic_id": str(world["clinic_id"]),
            "name": "Retired",
            "duration_minutes": 30,
            "price_cents": 1000,
            "currency": "USD",
            "is_active": False,
        },
        headers=auth_header(world["admin_token"]),
    )
    assert created.status_code == 201
    body = {
        **_body(world, starts_at=_at(48), patient_id=str(world["patient_id"])),
        "service_id": created.json()["id"],
    }
    response = await client.post(
        "/api/v1/appointments", json=body, headers=auth_header(world["admin_token"])
    )
    assert response.status_code == 422


async def test_clinician_cannot_book_outside_their_clinic(
    client: httpx.AsyncClient, world: dict[str, Any], seed: Any, login: Any
) -> None:
    # A clinician in a different clinic tries to book in `world`'s clinic.
    other_clinic = await seed.clinic(timezone="UTC")
    other_doc_user = await seed.user(role="CLINICIAN", email="otherdoc@ap.io")
    await seed.clinician(
        clinic_id=other_clinic, user_id=other_doc_user, working_hours=[(0, "00:00", "23:59")]
    )
    other_doc_token = await login("otherdoc@ap.io")
    response = await _book(
        client, world, other_doc_token, starts_at=_at(48), patient_id=str(world["patient_id"])
    )
    assert response.status_code == 403


async def test_booking_outside_working_hours_is_422(
    client: httpx.AsyncClient, seed: Any, login: Any
) -> None:
    await seed.user(role="PLATFORM_ADMIN", email="adminwh@ap.io")
    patient_id = await seed.user(role="PATIENT", email="patwh@ap.io")
    doc_user = await seed.user(role="CLINICIAN", email="docwh@ap.io")
    clinic_id = await seed.clinic(timezone="UTC")
    clinician_id = await seed.clinician(
        clinic_id=clinic_id,
        user_id=doc_user,
        working_hours=[(wd, "09:00", "10:00") for wd in range(7)],
    )
    service_id = await seed.service(clinic_id=clinic_id, duration_minutes=30)
    token = await login("adminwh@ap.io")
    response = await client.post(
        "/api/v1/appointments",
        json={
            "clinic_id": str(clinic_id),
            "clinician_id": str(clinician_id),
            "service_id": str(service_id),
            "patient_id": str(patient_id),
            "starts_at": "2035-06-10T03:00:00+00:00",  # 03:00, well outside 09:00-10:00
        },
        headers=auth_header(token),
    )
    assert response.status_code == 422


async def test_overlapping_booking_is_409(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    starts_at = _at(72)
    first = await _book(
        client,
        world,
        world["admin_token"],
        starts_at=starts_at,
        patient_id=str(world["patient_id"]),
    )
    assert first.status_code == 201
    second = await _book(
        client,
        world,
        world["admin_token"],
        starts_at=starts_at,
        patient_id=str(world["patient_id"]),
    )
    assert second.status_code == 409


# ---- list ----


async def test_list_scoping_by_role(
    client: httpx.AsyncClient, world: dict[str, Any], seed: Any, login: Any
) -> None:
    await _book(client, world, world["patient_token"], starts_at=_at(48))

    admin_list = await client.get("/api/v1/appointments", headers=auth_header(world["admin_token"]))
    assert admin_list.status_code == 200
    assert len(admin_list.json()["data"]) == 1

    patient_list = await client.get(
        "/api/v1/appointments", headers=auth_header(world["patient_token"])
    )
    assert patient_list.status_code == 200
    assert len(patient_list.json()["data"]) == 1

    doc_list = await client.get("/api/v1/appointments", headers=auth_header(world["doc_token"]))
    assert doc_list.status_code == 200
    assert len(doc_list.json()["data"]) == 1

    # A clinician user with no clinician row sees nothing, not an error.
    await seed.user(role="CLINICIAN", email="rowless@ap.io")
    rowless = await login("rowless@ap.io")
    rowless_list = await client.get("/api/v1/appointments", headers=auth_header(rowless))
    assert rowless_list.status_code == 200
    assert rowless_list.json()["data"] == []


# ---- read ----


async def test_read_scoping(
    client: httpx.AsyncClient, world: dict[str, Any], seed: Any, login: Any
) -> None:
    created = await _book(client, world, world["patient_token"], starts_at=_at(48))
    appt_id = created.json()["id"]

    missing = await client.get(
        f"/api/v1/appointments/{_RANDOM}", headers=auth_header(world["admin_token"])
    )
    assert missing.status_code == 404

    owner = await client.get(
        f"/api/v1/appointments/{appt_id}", headers=auth_header(world["patient_token"])
    )
    assert owner.status_code == 200
    assert owner.headers["etag"] == '"1"'

    doc = await client.get(
        f"/api/v1/appointments/{appt_id}", headers=auth_header(world["doc_token"])
    )
    assert doc.status_code == 200

    other_patient = await seed.user(role="PATIENT", email="other@ap.io")
    assert other_patient  # created
    other_token = await login("other@ap.io")
    forbidden = await client.get(
        f"/api/v1/appointments/{appt_id}", headers=auth_header(other_token)
    )
    assert forbidden.status_code == 403


async def test_clinician_from_another_clinic_cannot_read(
    client: httpx.AsyncClient, world: dict[str, Any], seed: Any, login: Any
) -> None:
    created = await _book(client, world, world["patient_token"], starts_at=_at(48))
    appt_id = created.json()["id"]
    other_clinic = await seed.clinic(timezone="UTC")
    other_doc_user = await seed.user(role="CLINICIAN", email="fardoc@ap.io")
    await seed.clinician(
        clinic_id=other_clinic, user_id=other_doc_user, working_hours=[(0, "00:00", "23:59")]
    )
    far_token = await login("fardoc@ap.io")
    response = await client.get(f"/api/v1/appointments/{appt_id}", headers=auth_header(far_token))
    assert response.status_code == 403


# ---- transition ----


async def test_transition_flow(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    created = await _book(client, world, world["patient_token"], starts_at=_at(48))
    appt_id = created.json()["id"]
    admin = auth_header(world["admin_token"])
    url = f"/api/v1/appointments/{appt_id}/transition"

    # Missing / stale If-Match.
    assert (
        await client.post(url, json={"target_status": "CONFIRMED"}, headers=admin)
    ).status_code == 428
    stale = await client.post(
        url, json={"target_status": "CONFIRMED"}, headers={**admin, "If-Match": '"999"'}
    )
    assert stale.status_code == 412

    # CANCELLED via transition is refused.
    refused = await client.post(
        url, json={"target_status": "CANCELLED"}, headers={**admin, "If-Match": '"1"'}
    )
    assert refused.status_code == 422

    # Happy: REQUESTED -> CONFIRMED bumps the version and ETag.
    ok = await client.post(
        url, json={"target_status": "CONFIRMED"}, headers={**admin, "If-Match": '"1"'}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "CONFIRMED"
    assert ok.json()["version"] == 2
    assert ok.headers["etag"] == '"2"'

    # Illegal: CONFIRMED -> REQUESTED.
    illegal = await client.post(
        url, json={"target_status": "REQUESTED"}, headers={**admin, "If-Match": '"2"'}
    )
    assert illegal.status_code == 409


async def test_patient_cannot_transition(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    created = await _book(client, world, world["patient_token"], starts_at=_at(48))
    appt_id = created.json()["id"]
    response = await client.post(
        f"/api/v1/appointments/{appt_id}/transition",
        json={"target_status": "CONFIRMED"},
        headers=auth_header(world["patient_token"]),
    )
    assert response.status_code == 403


async def test_transition_unknown_is_404(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    response = await client.post(
        f"/api/v1/appointments/{_RANDOM}/transition",
        json={"target_status": "CONFIRMED"},
        headers={**auth_header(world["admin_token"]), "If-Match": '"1"'},
    )
    assert response.status_code == 404


# ---- cancel ----


async def test_cancel_happy_and_terminal(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    created = await _book(client, world, world["patient_token"], starts_at=_at(48))
    appt_id = created.json()["id"]
    headers = {**auth_header(world["patient_token"]), "If-Match": '"1"'}
    cancelled = await client.post(
        f"/api/v1/appointments/{appt_id}/cancel", json={"reason": "changed plans"}, headers=headers
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["cancellation_reason"] == "changed plans"

    # Cancelling again is an illegal transition.
    again = await client.post(
        f"/api/v1/appointments/{appt_id}/cancel",
        json={},
        headers={**auth_header(world["patient_token"]), "If-Match": '"2"'},
    )
    assert again.status_code == 409


async def test_patient_cancel_outside_window_is_403(
    client: httpx.AsyncClient, world: dict[str, Any]
) -> None:
    created = await _book(client, world, world["patient_token"], starts_at=_at(1))  # < 24h cutoff
    appt_id = created.json()["id"]
    response = await client.post(
        f"/api/v1/appointments/{appt_id}/cancel",
        json={},
        headers={**auth_header(world["patient_token"]), "If-Match": '"1"'},
    )
    assert response.status_code == 403


async def test_cancel_preconditions_and_not_found(
    client: httpx.AsyncClient, world: dict[str, Any]
) -> None:
    created = await _book(client, world, world["patient_token"], starts_at=_at(48))
    appt_id = created.json()["id"]
    admin = auth_header(world["admin_token"])

    assert (
        await client.post(f"/api/v1/appointments/{appt_id}/cancel", json={}, headers=admin)
    ).status_code == 428
    stale = await client.post(
        f"/api/v1/appointments/{appt_id}/cancel", json={}, headers={**admin, "If-Match": '"999"'}
    )
    assert stale.status_code == 412
    missing = await client.post(
        f"/api/v1/appointments/{_RANDOM}/cancel", json={}, headers={**admin, "If-Match": '"1"'}
    )
    assert missing.status_code == 404
