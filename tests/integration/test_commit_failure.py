"""A write request whose COMMIT fails (ADR 0017), end to end against real Postgres.

The commit runs before the response is sent, so a refused COMMIT reaches the client as a problem
document (500, or 409 for a constraint violation) and never as a success. Nothing from that
request is saved. Cancel, book, create clinic and delete webhook subscription represent the
write endpoints that use ``SessionDep``.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from appointments_api.api.deps import get_clock
from appointments_api.models import Appointment, AuditLogEntry, Clinic, WebhookSubscription
from appointments_api.repositories.redis_webhooks import RedisEventQueue
from tests.fakes.clock import FixedClock
from tests.integration.conftest import CommitFailure
from tests.integration.helpers import auth_header

ALL_WEEK = [(wd, "06:00", "22:00") for wd in range(7)]
NOW = datetime(2027, 6, 1, 9, 0, tzinfo=UTC)
CANCEL_SLOT = "2027-06-05T10:00:00+00:00"
BOOK_SLOT = "2027-06-06T10:00:00+00:00"
CONFIRM_SLOT = "2027-06-07T10:00:00+00:00"
CANCEL_OK_SLOT = "2027-06-08T10:00:00+00:00"
FAILING_CLINIC = "Commit Fails Clinic"
CONFLICTING_CLINIC = "Commit Conflicts Clinic"
PROBLEM_JSON = "application/problem+json"

_SUBSCRIPTION = {
    "url": "https://example.test/hook",
    "secret": "0123456789abcdef",
    "event_types": ["appointment.created"],
    "clinic_id": None,
}


@pytest.fixture
async def world(app: FastAPI, seed: Any, login: Any) -> dict[str, Any]:
    """One bookable clinic, a patient and a platform admin. The clock is fixed at ``NOW``."""
    app.dependency_overrides[get_clock] = lambda: FixedClock(NOW)
    patient_id = await seed.user(role="PATIENT", email="patient@x.io")
    await seed.user(role="PLATFORM_ADMIN", email="admin@x.io")
    doc_user = await seed.user(role="CLINICIAN", email="doc@x.io")
    clinic_id = await seed.clinic(timezone="UTC", cutoff_hours=24)
    clinician_id = await seed.clinician(
        clinic_id=clinic_id, user_id=doc_user, working_hours=ALL_WEEK
    )
    service_id = await seed.service(clinic_id=clinic_id, duration_minutes=30)
    return {
        "patient_id": patient_id,
        "clinic_id": clinic_id,
        "clinician_id": clinician_id,
        "service_id": service_id,
        "patient_token": await login("patient@x.io"),
        "admin_token": await login("admin@x.io"),
    }


def _booking(world: dict[str, Any], starts_at: str) -> dict[str, str]:
    return {
        "clinic_id": str(world["clinic_id"]),
        "clinician_id": str(world["clinician_id"]),
        "service_id": str(world["service_id"]),
        "starts_at": starts_at,
    }


async def _book(
    client: httpx.AsyncClient, world: dict[str, Any], starts_at: str
) -> tuple[str, str]:
    resp = await client.post(
        "/api/v1/appointments",
        json=_booking(world, starts_at),
        headers=auth_header(world["patient_token"]),
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"]), resp.headers["ETag"]


async def _subscribe(client: httpx.AsyncClient, world: dict[str, Any]) -> str:
    resp = await client.post(
        "/api/v1/webhooks/subscriptions",
        json=_SUBSCRIPTION,
        headers=auth_header(world["admin_token"]),
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _clinic_body(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "timezone": "UTC",
        "address": "1 Fail St",
        "cancellation_cutoff_hours": 24,
    }


async def _arm_cancel(fail_commit: CommitFailure, appt_id: str) -> None:
    target = uuid.UUID(appt_id)  # validated UUID, safe to inline
    await fail_commit.arm(
        table="appointments",
        event="UPDATE",
        when=f"NEW.id = '{target}'::uuid AND NEW.status::text = 'CANCELLED'",
    )


async def _arm_book(fail_commit: CommitFailure, world: dict[str, Any]) -> None:
    patient = uuid.UUID(str(world["patient_id"]))
    await fail_commit.arm(
        table="appointments",
        event="INSERT",
        when=f"NEW.patient_id = '{patient}'::uuid AND NEW.starts_at = '{BOOK_SLOT}'::timestamptz",
    )


async def _arm_clinic(fail_commit: CommitFailure, name: str, mode: Any = "error") -> None:
    await fail_commit.arm(table="clinics", event="INSERT", when=f"NEW.name = '{name}'", mode=mode)


async def _arm_unsubscribe(fail_commit: CommitFailure, sub_id: str) -> None:
    target = uuid.UUID(sub_id)
    await fail_commit.arm(
        table="webhook_subscriptions", event="DELETE", when=f"OLD.id = '{target}'::uuid"
    )


def _assert_problem(resp: httpx.Response, status: int) -> None:
    assert resp.status_code == status, (resp.status_code, resp.text)
    assert resp.headers["content-type"].startswith(PROBLEM_JSON), resp.headers
    body = resp.json()
    assert body["status"] == status, body
    assert body["type"].startswith("https://aurora.example/problems/"), body


@pytest.mark.agent_trusted
async def test_ac1_api_failed_commit_returns_problem_not_success(
    client: httpx.AsyncClient,
    raw_client: httpx.AsyncClient,
    world: dict[str, Any],
    fail_commit: CommitFailure,
) -> None:
    patient = auth_header(world["patient_token"])
    admin = auth_header(world["admin_token"])
    appt_id, etag = await _book(client, world, CANCEL_SLOT)
    sub_id = await _subscribe(client, world)
    await _arm_cancel(fail_commit, appt_id)
    await _arm_book(fail_commit, world)
    await _arm_clinic(fail_commit, FAILING_CLINIC)
    await _arm_clinic(fail_commit, CONFLICTING_CLINIC, mode="integrity")
    await _arm_unsubscribe(fail_commit, sub_id)

    # Cancel: 500 problem+json, no CANCELLED body.
    resp = await raw_client.post(
        f"/api/v1/appointments/{appt_id}/cancel",
        json={"reason": "cannot make it"},
        headers={**patient, "If-Match": etag},
    )
    _assert_problem(resp, 500)
    assert "CANCELLED" not in resp.text

    # Book: 500 problem+json, not 201.
    resp = await raw_client.post(
        "/api/v1/appointments", json=_booking(world, BOOK_SLOT), headers=patient
    )
    _assert_problem(resp, 500)

    # Create clinic: 500 problem+json.
    resp = await raw_client.post(
        "/api/v1/clinics", json=_clinic_body(FAILING_CLINIC), headers=admin
    )
    _assert_problem(resp, 500)

    # Delete webhook subscription: 500 problem+json, not 204.
    resp = await raw_client.delete(f"/api/v1/webhooks/subscriptions/{sub_id}", headers=admin)
    _assert_problem(resp, 500)

    # A constraint violation at COMMIT: 409 problem+json.
    resp = await raw_client.post(
        "/api/v1/clinics", json=_clinic_body(CONFLICTING_CLINIC), headers=admin
    )
    _assert_problem(resp, 409)


@pytest.mark.agent_trusted
async def test_ac3_data_failed_commit_saves_nothing(
    app: FastAPI,
    client: httpx.AsyncClient,
    raw_client: httpx.AsyncClient,
    world: dict[str, Any],
    fail_commit: CommitFailure,
) -> None:
    patient = auth_header(world["patient_token"])
    admin = auth_header(world["admin_token"])
    appt_id, etag = await _book(client, world, CANCEL_SLOT)
    sub_id = await _subscribe(client, world)
    await _arm_cancel(fail_commit, appt_id)
    await _arm_book(fail_commit, world)
    await _arm_clinic(fail_commit, FAILING_CLINIC)
    await _arm_unsubscribe(fail_commit, sub_id)

    await raw_client.post(
        f"/api/v1/appointments/{appt_id}/cancel",
        json={"reason": "cannot make it"},
        headers={**patient, "If-Match": etag},
    )
    await raw_client.post("/api/v1/appointments", json=_booking(world, BOOK_SLOT), headers=patient)
    await raw_client.post("/api/v1/clinics", json=_clinic_body(FAILING_CLINIC), headers=admin)
    await raw_client.delete(f"/api/v1/webhooks/subscriptions/{sub_id}", headers=admin)

    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as session:
        # Cancel: still REQUESTED, reason unchanged, and no audit row for it.
        appointment = await session.get(Appointment, uuid.UUID(appt_id))
        assert appointment is not None
        assert appointment.status.value == "REQUESTED"
        assert appointment.cancellation_reason is None
        audit_rows = await session.scalar(
            select(func.count())
            .select_from(AuditLogEntry)
            .where(AuditLogEntry.entity_id == appt_id)
        )
        assert audit_rows == 0

        # Book: no appointment for that patient and slot.
        booked = await session.scalar(
            select(func.count())
            .select_from(Appointment)
            .where(
                Appointment.patient_id == world["patient_id"],
                Appointment.starts_at == datetime.fromisoformat(BOOK_SLOT),
            )
        )
        assert booked == 0

        # Create clinic: no clinic with that name.
        clinics = await session.scalar(
            select(func.count()).select_from(Clinic).where(Clinic.name == FAILING_CLINIC)
        )
        assert clinics == 0

        # Delete webhook subscription: it still exists.
        assert await session.get(WebhookSubscription, uuid.UUID(sub_id)) is not None


async def _queued_events(app: FastAPI) -> list[dict[str, Any]]:
    raw = await app.state.redis.lrange(RedisEventQueue.KEY, 0, -1)
    return [json.loads(item) for item in raw]


@pytest.mark.agent_trusted
async def test_ac4_data_failed_commit_publishes_no_webhook_event(
    app: FastAPI,
    client: httpx.AsyncClient,
    raw_client: httpx.AsyncClient,
    world: dict[str, Any],
    fail_commit: CommitFailure,
) -> None:
    """Only the Redis event queue (testcontainer) is read; no worker runs, nothing is delivered."""
    patient = auth_header(world["patient_token"])
    admin = auth_header(world["admin_token"])
    cancel_id, cancel_etag = await _book(client, world, CANCEL_SLOT)
    confirm_id, confirm_etag = await _book(client, world, CONFIRM_SLOT)
    ok_id, ok_etag = await _book(client, world, CANCEL_OK_SLOT)
    await _arm_cancel(fail_commit, cancel_id)
    await _arm_book(fail_commit, world)
    await fail_commit.arm(
        table="appointments",
        event="UPDATE",
        when=f"NEW.id = '{uuid.UUID(confirm_id)}'::uuid AND NEW.status::text = 'CONFIRMED'",
    )
    # Drop the appointment.created events from the setup bookings.
    await app.state.redis.delete(RedisEventQueue.KEY)

    # Cancel, COMMIT fails: no appointment.cancelled event.
    resp = await raw_client.post(
        f"/api/v1/appointments/{cancel_id}/cancel",
        json={"reason": "cannot make it"},
        headers={**patient, "If-Match": cancel_etag},
    )
    assert resp.status_code == 500, resp.text
    events = await _queued_events(app)
    assert [e for e in events if e["type"] == "appointment.cancelled"] == [], events

    # Book, COMMIT fails: no appointment.created event.
    resp = await raw_client.post(
        "/api/v1/appointments", json=_booking(world, BOOK_SLOT), headers=patient
    )
    assert resp.status_code == 500, resp.text
    events = await _queued_events(app)
    assert [e for e in events if e["type"] == "appointment.created"] == [], events

    # Transition to CONFIRMED, COMMIT fails: no appointment.confirmed event.
    resp = await raw_client.post(
        f"/api/v1/appointments/{confirm_id}/transition",
        json={"target_status": "CONFIRMED"},
        headers={**admin, "If-Match": confirm_etag},
    )
    assert resp.status_code == 500, resp.text
    events = await _queued_events(app)
    assert [e for e in events if e["type"] == "appointment.confirmed"] == [], events
    assert events == [], events

    # Cancel, COMMIT succeeds: exactly one appointment.cancelled event, matching the saved row.
    resp = await raw_client.post(
        f"/api/v1/appointments/{ok_id}/cancel",
        json={"reason": "changed plans"},
        headers={**patient, "If-Match": ok_etag},
    )
    assert resp.status_code == 200, resp.text
    events = await _queued_events(app)
    assert len(events) == 1, events
    assert events[0]["type"] == "appointment.cancelled"
    saved = await client.get(f"/api/v1/appointments/{ok_id}", headers=admin)
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "CANCELLED"
    assert events[0]["data"]["appointment"] == saved.json()


RETRY_SLOT = "2027-06-09T10:00:00+00:00"
OTHER_BODY_SLOT = "2027-06-10T10:00:00+00:00"


async def _arm_book_at(fail_commit: CommitFailure, world: dict[str, Any], starts_at: str) -> None:
    patient = uuid.UUID(str(world["patient_id"]))
    slot = datetime.fromisoformat(starts_at).isoformat()  # validated timestamp, safe to inline
    await fail_commit.arm(
        table="appointments",
        event="INSERT",
        when=f"NEW.patient_id = '{patient}'::uuid AND NEW.starts_at = '{slot}'::timestamptz",
    )


async def _patient_slots(client: httpx.AsyncClient, world: dict[str, Any]) -> list[datetime]:
    resp = await client.get("/api/v1/appointments", headers=auth_header(world["patient_token"]))
    assert resp.status_code == 200, resp.text
    return [datetime.fromisoformat(a["starts_at"]) for a in resp.json()["data"]]


@pytest.mark.agent_trusted
async def test_ac2_api_failed_commit_frees_the_idempotency_key(
    raw_client: httpx.AsyncClient,
    world: dict[str, Any],
    fail_commit: CommitFailure,
) -> None:
    patient = auth_header(world["patient_token"])
    key = {"Idempotency-Key": "commit-fails-k"}

    # Book with key K and the COMMIT fails: 500 problem+json, and no appointment is found.
    await _arm_book_at(fail_commit, world, RETRY_SLOT)
    resp = await raw_client.post(
        "/api/v1/appointments", json=_booking(world, RETRY_SLOT), headers={**patient, **key}
    )
    _assert_problem(resp, 500)
    assert datetime.fromisoformat(RETRY_SLOT) not in await _patient_slots(raw_client, world)

    # Retry the same body with K and the COMMIT succeeds: 201 with a new, saved appointment.
    await fail_commit.disarm()
    retry = await raw_client.post(
        "/api/v1/appointments", json=_booking(world, RETRY_SLOT), headers={**patient, **key}
    )
    assert retry.status_code == 201, retry.text
    assert retry.headers.get("idempotency-replayed") is None, retry.headers
    appt_id = retry.json()["id"]
    saved = await raw_client.get(f"/api/v1/appointments/{appt_id}", headers=patient)
    assert saved.status_code == 200, saved.text
    assert saved.json()["id"] == appt_id
    assert datetime.fromisoformat(saved.json()["starts_at"]) == datetime.fromisoformat(RETRY_SLOT)

    # COMMIT succeeded, retry the same body with K: 201 replay with the same appointment id.
    replay = await raw_client.post(
        "/api/v1/appointments", json=_booking(world, RETRY_SLOT), headers={**patient, **key}
    )
    assert replay.status_code == 201, replay.text
    assert replay.headers.get("idempotency-replayed") == "true", replay.headers
    assert replay.json()["id"] == appt_id

    # Book with a second key K2 and the COMMIT fails, then a different body with K2: 201, not 422.
    other_key = {"Idempotency-Key": "commit-fails-k2"}
    await _arm_book_at(fail_commit, world, BOOK_SLOT)
    resp = await raw_client.post(
        "/api/v1/appointments", json=_booking(world, BOOK_SLOT), headers={**patient, **other_key}
    )
    _assert_problem(resp, 500)
    different = await raw_client.post(
        "/api/v1/appointments",
        json=_booking(world, OTHER_BODY_SLOT),
        headers={**patient, **other_key},
    )
    assert different.status_code == 201, different.text
    assert different.headers.get("idempotency-replayed") is None, different.headers
    new_id = different.json()["id"]
    assert new_id != appt_id
    saved = await raw_client.get(f"/api/v1/appointments/{new_id}", headers=patient)
    assert saved.status_code == 200, saved.text
    assert datetime.fromisoformat(saved.json()["starts_at"]) == datetime.fromisoformat(
        OTHER_BODY_SLOT
    )
