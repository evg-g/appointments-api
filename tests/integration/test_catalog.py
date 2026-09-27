"""Catalog endpoints: clinics, clinicians, services, users, availability.

Covers the create/list/read happy paths, their not-found and validation branches, and per-object
read authorization — the parts of these routers the milestone-3/4 suites did not reach.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from tests.integration.helpers import auth_header

_RANDOM = str(uuid.uuid4())


@pytest.fixture
async def admin(client: httpx.AsyncClient, seed: Any, login: Any) -> str:
    await seed.user(role="PLATFORM_ADMIN", email="admin@cat.io")
    token: str = await login("admin@cat.io")
    return token


# ---- clinicians ----


async def test_clinician_create_list_read(client: httpx.AsyncClient, admin: str, seed: Any) -> None:
    clinic_id = await seed.clinic()
    user_id = await seed.user(role="CLINICIAN", email="doc-cat@x.io")
    created = await client.post(
        "/api/v1/clinicians",
        json={
            "clinic_id": str(clinic_id),
            "user_id": str(user_id),
            "specialty": "GP",
            "buffer_minutes": 0,
            "working_hours": [{"weekday": 0, "start": "09:00:00", "end": "17:00:00"}],
        },
        headers=auth_header(admin),
    )
    assert created.status_code == 201, created.text
    clinician_id = created.json()["id"]
    assert created.json()["working_hours"][0]["weekday"] == 0

    listing = await client.get(
        "/api/v1/clinicians", params={"clinic_id": str(clinic_id)}, headers=auth_header(admin)
    )
    assert listing.status_code == 200
    assert any(row["id"] == clinician_id for row in listing.json()["data"])

    read = await client.get(f"/api/v1/clinicians/{clinician_id}", headers=auth_header(admin))
    assert read.status_code == 200
    assert read.json()["id"] == clinician_id


async def test_clinician_working_hours_end_must_be_after_start(
    client: httpx.AsyncClient, admin: str, seed: Any
) -> None:
    clinic_id = await seed.clinic()
    user_id = await seed.user(role="CLINICIAN", email="doc-bad@x.io")
    response = await client.post(
        "/api/v1/clinicians",
        json={
            "clinic_id": str(clinic_id),
            "user_id": str(user_id),
            "specialty": "GP",
            "working_hours": [{"weekday": 0, "start": "17:00:00", "end": "09:00:00"}],
        },
        headers=auth_header(admin),
    )
    assert response.status_code == 422


async def test_clinician_read_unknown_is_404(client: httpx.AsyncClient, admin: str) -> None:
    response = await client.get(f"/api/v1/clinicians/{_RANDOM}", headers=auth_header(admin))
    assert response.status_code == 404


# ---- clinics ----


async def test_clinic_list_and_read(client: httpx.AsyncClient, admin: str, seed: Any) -> None:
    clinic_id = await seed.clinic()
    listing = await client.get("/api/v1/clinics", headers=auth_header(admin))
    assert listing.status_code == 200
    assert any(row["id"] == str(clinic_id) for row in listing.json()["data"])

    read = await client.get(f"/api/v1/clinics/{clinic_id}", headers=auth_header(admin))
    assert read.status_code == 200


async def test_clinic_read_unknown_is_404(client: httpx.AsyncClient, admin: str) -> None:
    response = await client.get(f"/api/v1/clinics/{_RANDOM}", headers=auth_header(admin))
    assert response.status_code == 404


# ---- services ----


async def test_service_create_list_read(client: httpx.AsyncClient, admin: str, seed: Any) -> None:
    clinic_id = await seed.clinic()
    created = await client.post(
        "/api/v1/services",
        json={
            "clinic_id": str(clinic_id),
            "name": "Consult",
            "duration_minutes": 30,
            "price_cents": 5000,
            "currency": "USD",
            "is_active": True,
        },
        headers=auth_header(admin),
    )
    assert created.status_code == 201, created.text
    service_id = created.json()["id"]

    listing = await client.get(
        "/api/v1/services", params={"clinic_id": str(clinic_id)}, headers=auth_header(admin)
    )
    assert listing.status_code == 200
    assert any(row["id"] == service_id for row in listing.json()["data"])

    read = await client.get(f"/api/v1/services/{service_id}", headers=auth_header(admin))
    assert read.status_code == 200


async def test_service_read_unknown_is_404(client: httpx.AsyncClient, admin: str) -> None:
    response = await client.get(f"/api/v1/services/{_RANDOM}", headers=auth_header(admin))
    assert response.status_code == 404


# ---- users ----


async def test_user_create_duplicate_email_is_409(
    client: httpx.AsyncClient, admin: str, seed: Any
) -> None:
    await seed.user(role="PATIENT", email="dupe@x.io")
    response = await client.post(
        "/api/v1/users",
        json={
            "email": "dupe@x.io",
            "full_name": "Dupe",
            "role": "PATIENT",
            "password": "password123",
        },
        headers=auth_header(admin),
    )
    assert response.status_code == 409


async def test_user_can_read_self_but_not_others(
    client: httpx.AsyncClient, seed: Any, login: Any
) -> None:
    a_id = await seed.user(role="PATIENT", email="a@u.io")
    b_id = await seed.user(role="PATIENT", email="b@u.io")
    a_token = await login("a@u.io")

    own = await client.get(f"/api/v1/users/{a_id}", headers=auth_header(a_token))
    assert own.status_code == 200
    assert own.json()["email"] == "a@u.io"

    other = await client.get(f"/api/v1/users/{b_id}", headers=auth_header(a_token))
    assert other.status_code == 403


async def test_admin_reads_any_user_and_404_for_unknown(
    client: httpx.AsyncClient, admin: str, seed: Any
) -> None:
    some_id = await seed.user(role="PATIENT", email="some@u.io")
    ok = await client.get(f"/api/v1/users/{some_id}", headers=auth_header(admin))
    assert ok.status_code == 200
    missing = await client.get(f"/api/v1/users/{_RANDOM}", headers=auth_header(admin))
    assert missing.status_code == 404


# ---- availability ----


async def test_availability_happy_path(client: httpx.AsyncClient, admin: str, seed: Any) -> None:
    clinic_id = await seed.clinic(timezone="UTC")
    user_id = await seed.user(role="CLINICIAN", email="avail-doc@x.io")
    clinician_id = await seed.clinician(
        clinic_id=clinic_id,
        user_id=user_id,
        working_hours=[(wd, "09:00", "17:00") for wd in range(7)],
    )
    service_id = await seed.service(clinic_id=clinic_id, duration_minutes=30)
    response = await client.get(
        "/api/v1/availability",
        params={
            "clinician_id": str(clinician_id),
            "service_id": str(service_id),
            "day": "2035-01-10",
        },
        headers=auth_header(admin),
    )
    assert response.status_code == 200
    slots = response.json()
    assert isinstance(slots, list)
    assert slots and "start" in slots[0] and "end" in slots[0]


async def test_availability_unknown_clinician_is_404(
    client: httpx.AsyncClient, admin: str, seed: Any
) -> None:
    clinic_id = await seed.clinic()
    service_id = await seed.service(clinic_id=clinic_id)
    response = await client.get(
        "/api/v1/availability",
        params={"clinician_id": _RANDOM, "service_id": str(service_id), "day": "2035-01-10"},
        headers=auth_header(admin),
    )
    assert response.status_code == 404


async def test_availability_unknown_service_is_404(
    client: httpx.AsyncClient, admin: str, seed: Any
) -> None:
    clinic_id = await seed.clinic()
    user_id = await seed.user(role="CLINICIAN", email="avail-doc2@x.io")
    clinician_id = await seed.clinician(
        clinic_id=clinic_id, user_id=user_id, working_hours=[(0, "09:00", "17:00")]
    )
    response = await client.get(
        "/api/v1/availability",
        params={"clinician_id": str(clinician_id), "service_id": _RANDOM, "day": "2035-01-10"},
        headers=auth_header(admin),
    )
    assert response.status_code == 404
