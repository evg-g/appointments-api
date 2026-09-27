"""Idempotency-Key on appointment create, end to end against the real Redis container (spec rule 6).

The same key replayed returns the original resource; the same key with a different body is rejected;
and a failed first attempt frees the key so a corrected retry can proceed.
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


async def test_same_key_replays_original_and_creates_only_one(
    client: httpx.AsyncClient, booking_env: dict[str, Any]
) -> None:
    headers = {**auth_header(booking_env["patient_token"]), "Idempotency-Key": "book-001"}
    body = _body(booking_env, "2027-06-01T10:00:00+00:00")

    first = await client.post("/api/v1/appointments", json=body, headers=headers)
    assert first.status_code == 201
    assert first.headers.get("Idempotency-Replayed") is None

    second = await client.post("/api/v1/appointments", json=body, headers=headers)
    assert second.status_code == 201
    assert second.headers.get("Idempotency-Replayed") == "true"
    # Same resource, not a duplicate.
    assert second.json()["id"] == first.json()["id"]

    listed = await client.get(
        "/api/v1/appointments", headers=auth_header(booking_env["patient_token"])
    )
    assert len(listed.json()["data"]) == 1


async def test_same_key_different_body_is_rejected(
    client: httpx.AsyncClient, booking_env: dict[str, Any]
) -> None:
    headers = {**auth_header(booking_env["patient_token"]), "Idempotency-Key": "book-002"}

    first = await client.post(
        "/api/v1/appointments",
        json=_body(booking_env, "2027-06-01T10:00:00+00:00"),
        headers=headers,
    )
    assert first.status_code == 201

    reused = await client.post(
        "/api/v1/appointments",
        json=_body(booking_env, "2027-06-01T14:00:00+00:00"),  # different time, same key
        headers=headers,
    )
    assert reused.status_code == 422
    assert reused.json()["type"].endswith("/idempotency-key-reused")


async def test_failed_attempt_frees_the_key_for_a_retry(
    client: httpx.AsyncClient, booking_env: dict[str, Any]
) -> None:
    headers = {**auth_header(booking_env["patient_token"]), "Idempotency-Key": "book-003"}

    # First attempt is invalid (before the 06:00 working window) → 422, no resource created.
    bad = await client.post(
        "/api/v1/appointments",
        json=_body(booking_env, "2027-06-01T03:00:00+00:00"),
        headers=headers,
    )
    assert bad.status_code == 422

    # Same key, now a valid body: the key was released, so this succeeds instead of replaying the
    # failure or reporting "in progress".
    good = await client.post(
        "/api/v1/appointments",
        json=_body(booking_env, "2027-06-01T09:00:00+00:00"),
        headers=headers,
    )
    assert good.status_code == 201
    assert good.headers.get("Idempotency-Replayed") is None
