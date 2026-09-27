"""ETag / If-Match optimistic concurrency, end to end (spec rule 7).

Reads carry an ETag; a mutating request must echo the current ETag in If-Match. Missing → 428,
stale → 412, current → 200 with a new ETag.
"""

from __future__ import annotations

from typing import Any

import httpx

from tests.integration.helpers import auth_header


def _body(env: dict[str, Any], starts_at: str) -> dict[str, Any]:
    return {
        "clinic_id": str(env["clinic_id"]),
        "clinician_id": str(env["clinician_id"]),
        "service_id": str(env["service_id"]),
        "starts_at": starts_at,
    }


async def _create(client: httpx.AsyncClient, env: dict[str, Any], starts_at: str) -> httpx.Response:
    return await client.post(
        "/api/v1/appointments",
        json=_body(env, starts_at),
        headers=auth_header(env["patient_token"]),
    )


async def test_create_and_get_carry_an_etag(
    client: httpx.AsyncClient, booking_env: dict[str, Any]
) -> None:
    created = await _create(client, booking_env, "2027-06-01T10:00:00+00:00")
    assert created.status_code == 201
    assert created.headers["ETag"] == '"1"'

    got = await client.get(
        f"/api/v1/appointments/{created.json()['id']}",
        headers=auth_header(booking_env["patient_token"]),
    )
    assert got.status_code == 200
    assert got.headers["ETag"] == '"1"'


async def test_transition_without_if_match_is_428(
    client: httpx.AsyncClient, booking_env: dict[str, Any]
) -> None:
    created = await _create(client, booking_env, "2027-06-01T11:00:00+00:00")
    resp = await client.post(
        f"/api/v1/appointments/{created.json()['id']}/transition",
        json={"target_status": "CONFIRMED"},
        headers=auth_header(booking_env["admin_token"]),
    )
    assert resp.status_code == 428
    assert resp.json()["type"].endswith("/precondition-required")


async def test_transition_with_stale_if_match_is_412(
    client: httpx.AsyncClient, booking_env: dict[str, Any]
) -> None:
    created = await _create(client, booking_env, "2027-06-01T12:00:00+00:00")
    resp = await client.post(
        f"/api/v1/appointments/{created.json()['id']}/transition",
        json={"target_status": "CONFIRMED"},
        headers={**auth_header(booking_env["admin_token"]), "If-Match": '"999"'},
    )
    assert resp.status_code == 412
    assert resp.json()["type"].endswith("/precondition-failed")


async def test_conditional_update_succeeds_and_moves_the_etag(
    client: httpx.AsyncClient, booking_env: dict[str, Any]
) -> None:
    created = await _create(client, booking_env, "2027-06-01T13:00:00+00:00")
    appointment_id = created.json()["id"]
    etag_v1 = created.headers["ETag"]

    confirm = await client.post(
        f"/api/v1/appointments/{appointment_id}/transition",
        json={"target_status": "CONFIRMED"},
        headers={**auth_header(booking_env["admin_token"]), "If-Match": etag_v1},
    )
    assert confirm.status_code == 200
    etag_v2 = confirm.headers["ETag"]
    assert etag_v2 != etag_v1

    # Reusing the now-stale first ETag is refused: the resource has moved on.
    replay_old = await client.post(
        f"/api/v1/appointments/{appointment_id}/transition",
        json={"target_status": "COMPLETED"},
        headers={**auth_header(booking_env["admin_token"]), "If-Match": etag_v1},
    )
    assert replay_old.status_code == 412

    # With the fresh ETag it goes through.
    complete = await client.post(
        f"/api/v1/appointments/{appointment_id}/transition",
        json={"target_status": "COMPLETED"},
        headers={**auth_header(booking_env["admin_token"]), "If-Match": etag_v2},
    )
    assert complete.status_code == 200
    assert complete.json()["status"] == "COMPLETED"
