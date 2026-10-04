"""A write request whose COMMIT fails (ADR 0017), end to end against real Postgres.

The commit runs before the response is sent, so a refused COMMIT reaches the client as a problem
document (500, or 409 for a constraint violation) and never as a success. Nothing from that
request is saved. Cancel, book, create clinic and delete webhook subscription represent the
write endpoints that use ``SessionDep``.
"""

from __future__ import annotations

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
from tests.fakes.clock import FixedClock
from tests.integration.conftest import CommitFailure
from tests.integration.helpers import auth_header

ALL_WEEK = [(wd, "06:00", "22:00") for wd in range(7)]
NOW = datetime(2027, 6, 1, 9, 0, tzinfo=UTC)
CANCEL_SLOT = "2027-06-05T10:00:00+00:00"
BOOK_SLOT = "2027-06-06T10:00:00+00:00"
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
