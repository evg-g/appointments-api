"""Audit-log write on appointment cancel (ADR 0016), end to end against real Postgres.

A successful cancel records exactly one ``appointment.cancelled`` row in the same transaction; a
cancel refused inside the handler records nothing.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from appointments_api.api.deps import SessionDep, get_audit_log_repository, get_clock
from appointments_api.models import Appointment, AuditLogEntry
from appointments_api.repositories.audit_log import AuditLogRepository
from appointments_api.repositories.redis_webhooks import RedisEventQueue
from tests.fakes.clock import FixedClock
from tests.integration.conftest import CommitFailure
from tests.integration.helpers import auth_header

ALL_WEEK = [(wd, "06:00", "22:00") for wd in range(7)]
NOW = datetime(2027, 6, 1, 9, 0, tzinfo=UTC)
# Relative to NOW and the clinic's 24h cutoff.
INSIDE_CUTOFF = "2027-06-01T10:00:00+00:00"


def _outside_cutoff(day: int, hour: int = 10) -> str:
    return f"2027-06-{day:02d}T{hour:02d}:00:00+00:00"


@pytest.fixture
async def world(app: FastAPI, seed: Any, login: Any) -> dict[str, Any]:
    """Two clinics. Clinic A has a patient, a clinician and a clinic admin; clinic B has its own
    clinician. The clock is fixed at ``NOW`` so the 24h cutoff is deterministic."""
    app.dependency_overrides[get_clock] = lambda: FixedClock(NOW)
    patient_id = await seed.user(role="PATIENT", email="patient@x.io")
    other_patient_id = await seed.user(role="PATIENT", email="other@x.io")
    admin_id = await seed.user(role="CLINIC_ADMIN", email="clinic-admin@x.io")
    doc_user = await seed.user(role="CLINICIAN", email="doc@x.io")
    doc_b_user = await seed.user(role="CLINICIAN", email="doc-b@x.io")
    clinic_id = await seed.clinic(timezone="UTC", cutoff_hours=24)
    clinic_b_id = await seed.clinic(timezone="UTC", cutoff_hours=24)
    clinician_id = await seed.clinician(
        clinic_id=clinic_id, user_id=doc_user, working_hours=ALL_WEEK
    )
    await seed.clinician(clinic_id=clinic_b_id, user_id=doc_b_user, working_hours=ALL_WEEK)
    service_id = await seed.service(clinic_id=clinic_id, duration_minutes=30)
    return {
        "patient_id": patient_id,
        "other_patient_id": other_patient_id,
        "admin_id": admin_id,
        "doc_user_id": doc_user,
        "clinic_id": clinic_id,
        "clinician_id": clinician_id,
        "service_id": service_id,
        "patient_token": await login("patient@x.io"),
        "other_token": await login("other@x.io"),
        "admin_token": await login("clinic-admin@x.io"),
        "doc_token": await login("doc@x.io"),
        "doc_b_token": await login("doc-b@x.io"),
    }


async def _book(
    client: httpx.AsyncClient, world: dict[str, Any], starts_at: str, token: str | None = None
) -> tuple[str, str]:
    resp = await client.post(
        "/api/v1/appointments",
        json={
            "clinic_id": str(world["clinic_id"]),
            "clinician_id": str(world["clinician_id"]),
            "service_id": str(world["service_id"]),
            "starts_at": starts_at,
        },
        headers=auth_header(token or world["patient_token"]),
    )
    assert resp.status_code == 201, resp.json()
    return str(resp.json()["id"]), resp.headers["ETag"]


async def _transition(
    client: httpx.AsyncClient, world: dict[str, Any], appointment_id: str, etag: str, target: str
) -> str:
    resp = await client.post(
        f"/api/v1/appointments/{appointment_id}/transition",
        json={"target_status": target},
        headers={**auth_header(world["admin_token"]), "If-Match": etag},
    )
    assert resp.status_code == 200, resp.json()
    return resp.headers["ETag"]


async def _cancel(
    client: httpx.AsyncClient, appointment_id: str, token: str, etag: str | None
) -> httpx.Response:
    headers = auth_header(token)
    if etag is not None:
        headers["If-Match"] = etag
    return await client.post(
        f"/api/v1/appointments/{appointment_id}/cancel",
        json={"reason": "cannot make it"},
        headers=headers,
    )


async def _audit_rows(app: FastAPI, entity_id: str | None = None) -> Sequence[AuditLogEntry]:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as session:
        stmt = select(AuditLogEntry)
        if entity_id is not None:
            stmt = stmt.where(AuditLogEntry.entity_id == entity_id)
        return (await session.execute(stmt)).scalars().all()


def _assert_cancel_row(
    rows: Sequence[AuditLogEntry], *, actor_id: uuid.UUID, entity_id: str, before: str
) -> None:
    assert len(rows) == 1, [(r.action, r.entity_id) for r in rows]
    row = rows[0]
    assert row.actor_id == actor_id
    assert row.action == "appointment.cancelled"
    assert row.entity_type == "appointment"
    assert row.entity_id == entity_id
    assert row.before == {"status": before}
    assert row.after == {"status": "CANCELLED"}


@pytest.mark.agent_trusted
async def test_ac1_data_successful_cancel_records_one_audit_row(
    client: httpx.AsyncClient, app: FastAPI, world: dict[str, Any]
) -> None:
    # Patient cancels their own REQUESTED appointment outside the cutoff.
    req_id, req_etag = await _book(client, world, _outside_cutoff(5))
    resp = await _cancel(client, req_id, world["patient_token"], req_etag)
    assert resp.status_code == 200, resp.json()
    _assert_cancel_row(
        await _audit_rows(app, req_id),
        actor_id=world["patient_id"],
        entity_id=req_id,
        before="REQUESTED",
    )

    # A CLINIC_ADMIN cancels a CONFIRMED appointment inside the cutoff.
    near_id, near_etag = await _book(client, world, INSIDE_CUTOFF)
    near_etag = await _transition(client, world, near_id, near_etag, "CONFIRMED")
    resp = await _cancel(client, near_id, world["admin_token"], near_etag)
    assert resp.status_code == 200, resp.json()
    _assert_cancel_row(
        await _audit_rows(app, near_id),
        actor_id=world["admin_id"],
        entity_id=near_id,
        before="CONFIRMED",
    )

    # A clinician cancels a CONFIRMED appointment in their own clinic outside the cutoff.
    doc_id, doc_etag = await _book(client, world, _outside_cutoff(6))
    doc_etag = await _transition(client, world, doc_id, doc_etag, "CONFIRMED")
    resp = await _cancel(client, doc_id, world["doc_token"], doc_etag)
    assert resp.status_code == 200, resp.json()
    _assert_cancel_row(
        await _audit_rows(app, doc_id),
        actor_id=world["doc_user_id"],
        entity_id=doc_id,
        before="CONFIRMED",
    )

    # Different appointments cancelled: one row per entity_id, and nothing else was audited.
    all_rows = await _audit_rows(app)
    assert sorted(r.entity_id for r in all_rows) == sorted([req_id, near_id, doc_id])


@pytest.mark.agent_trusted
async def test_ac3_data_refused_cancel_records_no_audit_row(
    client: httpx.AsyncClient, app: FastAPI, world: dict[str, Any]
) -> None:
    patient = world["patient_token"]

    # Unknown appointment id -> 404, and the table stays empty.
    resp = await _cancel(client, str(uuid.uuid4()), patient, '"1"')
    assert resp.status_code == 404
    assert list(await _audit_rows(app)) == []

    # Another patient's appointment -> 403.
    other_id, other_etag = await _book(client, world, _outside_cutoff(5), world["other_token"])
    resp = await _cancel(client, other_id, patient, other_etag)
    assert resp.status_code == 403
    assert list(await _audit_rows(app, other_id)) == []

    # Own appointment inside the cutoff -> 403.
    near_id, near_etag = await _book(client, world, INSIDE_CUTOFF)
    resp = await _cancel(client, near_id, patient, near_etag)
    assert resp.status_code == 403
    assert list(await _audit_rows(app, near_id)) == []

    # No If-Match -> 428; stale If-Match -> 412.
    own_id, _ = await _book(client, world, _outside_cutoff(6))
    resp = await _cancel(client, own_id, patient, None)
    assert resp.status_code == 428
    resp = await _cancel(client, own_id, patient, '"999"')
    assert resp.status_code == 412
    assert list(await _audit_rows(app, own_id)) == []

    # COMPLETED and NO_SHOW are terminal -> 409.
    for day, terminal in ((7, "COMPLETED"), (8, "NO_SHOW")):
        t_id, t_etag = await _book(client, world, _outside_cutoff(day))
        t_etag = await _transition(client, world, t_id, t_etag, "CONFIRMED")
        t_etag = await _transition(client, world, t_id, t_etag, terminal)
        resp = await _cancel(client, t_id, world["admin_token"], t_etag)
        assert resp.status_code == 409, (terminal, resp.json())
        assert list(await _audit_rows(app, t_id)) == [], terminal

    # A clinician from another clinic -> 403.
    foreign_id, foreign_etag = await _book(client, world, _outside_cutoff(9))
    resp = await _cancel(client, foreign_id, world["doc_b_token"], foreign_etag)
    assert resp.status_code == 403
    assert list(await _audit_rows(app, foreign_id)) == []

    # Cancel succeeds, then a second cancel with the new ETag is 409: still exactly one row.
    twice_id, twice_etag = await _book(client, world, _outside_cutoff(10))
    first = await _cancel(client, twice_id, patient, twice_etag)
    assert first.status_code == 200, first.json()
    second = await _cancel(client, twice_id, patient, first.headers["ETag"])
    assert second.status_code == 409
    assert len(await _audit_rows(app, twice_id)) == 1

    # Two concurrent cancels with the same valid If-Match: one 200, one 412, exactly one row.
    race_id, race_etag = await _book(client, world, _outside_cutoff(11))
    r1, r2 = await asyncio.gather(
        _cancel(client, race_id, patient, race_etag),
        _cancel(client, race_id, patient, race_etag),
    )
    assert sorted([r1.status_code, r2.status_code]) == [200, 412]
    assert len(await _audit_rows(app, race_id)) == 1

    # Across the whole run, only the two successful cancels were audited.
    assert sorted(r.entity_id for r in await _audit_rows(app)) == sorted([twice_id, race_id])


async def _read_audit_log(
    client: httpx.AsyncClient, token: str, params: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    resp = await client.get("/api/v1/audit-log", params=params or {}, headers=auth_header(token))
    assert resp.status_code == 200, resp.json()
    data: list[dict[str, Any]] = resp.json()["data"]
    return data


def _assert_cancel_item(item: dict[str, Any], *, actor_id: uuid.UUID, entity_id: str) -> None:
    assert item["actor_id"] == str(actor_id)
    assert item["action"] == "appointment.cancelled"
    assert item["entity_type"] == "appointment"
    assert item["entity_id"] == entity_id
    assert item["before"] == {"status": "REQUESTED"}
    assert item["after"] == {"status": "CANCELLED"}
    uuid.UUID(item["id"])
    datetime.fromisoformat(item["created_at"])


@pytest.mark.agent_trusted
async def test_ac2_api_admin_reads_cancel_entry_and_filters_by_entity(
    client: httpx.AsyncClient, seed: Any, login: Any, world: dict[str, Any]
) -> None:
    await seed.user(role="PLATFORM_ADMIN", email="platform-admin@x.io")
    platform_token = await login("platform-admin@x.io")

    # Patient cancels X and Y (both REQUESTED, outside the cutoff).
    x_id, x_etag = await _book(client, world, _outside_cutoff(5))
    y_id, y_etag = await _book(client, world, _outside_cutoff(6))
    for appt_id, etag in ((x_id, x_etag), (y_id, y_etag)):
        resp = await _cancel(client, appt_id, world["patient_token"], etag)
        assert resp.status_code == 200, resp.json()

    # A CLINIC_ADMIN reads the unfiltered log: the entry for X is there with every field.
    items = await _read_audit_log(client, world["admin_token"])
    x_items = [i for i in items if i["entity_id"] == x_id]
    assert len(x_items) == 1, items
    _assert_cancel_item(x_items[0], actor_id=world["patient_id"], entity_id=x_id)

    # A PLATFORM_ADMIN filters by entity_id = X: exactly the entry for X.
    by_entity = await _read_audit_log(client, platform_token, {"entity_id": x_id})
    assert len(by_entity) == 1, by_entity
    _assert_cancel_item(by_entity[0], actor_id=world["patient_id"], entity_id=x_id)
    assert by_entity[0]["id"] == x_items[0]["id"]

    # Both admin roles, filtered by entity_type + action + entity_id: exactly the entry for X.
    combined = {"entity_type": "appointment", "action": "appointment.cancelled", "entity_id": x_id}
    for token in (world["admin_token"], platform_token):
        filtered = await _read_audit_log(client, token, combined)
        assert len(filtered) == 1, filtered
        _assert_cancel_item(filtered[0], actor_id=world["patient_id"], entity_id=x_id)
        assert filtered[0]["id"] == x_items[0]["id"]


class _FailingAuditLogRepository(AuditLogRepository):
    """Writes nothing: the audit insert fails inside the request, after the cancel was flushed."""

    async def add_entry(self, **_: Any) -> None:
        raise RuntimeError("audit_log insert failed")


@pytest.mark.agent_trusted
async def test_ac1_data_audit_write_failure_rolls_back_cancel(
    client: httpx.AsyncClient, app: FastAPI, world: dict[str, Any]
) -> None:
    # The cancel and its audit row share one transaction (ADR 0016): if the audit write fails, the
    # request is a 500, the appointment keeps its prior status, and no webhook event goes out.
    appt_id, etag = await _book(client, world, _outside_cutoff(5))
    await app.state.redis.delete(RedisEventQueue.KEY)  # ignore the created event

    def _failing_repo(session: SessionDep) -> AuditLogRepository:
        return _FailingAuditLogRepository(session)

    app.dependency_overrides[get_audit_log_repository] = _failing_repo
    # A client that returns the 500 response instead of re-raising the app's exception.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as raw:
        resp = await _cancel(raw, appt_id, world["patient_token"], etag)
    assert resp.status_code == 500, resp.text

    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as session:
        appointment = await session.get(Appointment, uuid.UUID(appt_id))
        assert appointment is not None
        assert appointment.status.value == "REQUESTED"
        assert appointment.cancellation_reason is None
    assert list(await _audit_rows(app, appt_id)) == []
    events = [json.loads(e) for e in await app.state.redis.lrange(RedisEventQueue.KEY, 0, -1)]
    assert [e for e in events if e["type"] == "appointment.cancelled"] == [], events


@pytest.mark.agent_trusted
async def test_ac1_data_failed_cancel_commit_leaves_no_audit_row(
    client: httpx.AsyncClient,
    raw_client: httpx.AsyncClient,
    app: FastAPI,
    world: dict[str, Any],
    fail_commit: CommitFailure,
) -> None:
    # The real audit wiring (no override): if the request's COMMIT fails after the audit write, the
    # audit row must roll back with the cancel: the cancel is not durable, and neither is its row.
    # A test-only deferred constraint trigger (the fail_commit fixture, ADR 0017) raises at COMMIT
    # time for this one appointment, so only a row written in the same transaction vanishes with it.
    appt_id, etag = await _book(client, world, _outside_cutoff(5))
    target = uuid.UUID(appt_id)  # validated UUID, safe to inline in the trigger body
    await fail_commit.arm(
        table="appointments",
        event="UPDATE",
        when=f"NEW.id = '{target}'::uuid AND NEW.status::text = 'CANCELLED'",
    )
    resp = await _cancel(raw_client, appt_id, world["patient_token"], etag)
    # The commit runs before the response (ADR 0017), so the refused COMMIT is a 500.
    assert resp.status_code == 500, (resp.status_code, resp.text)

    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as session:
        appointment = await session.get(Appointment, target)
        assert appointment is not None
        assert appointment.status.value == "REQUESTED"
        assert appointment.cancellation_reason is None
    assert list(await _audit_rows(app, appt_id)) == []
