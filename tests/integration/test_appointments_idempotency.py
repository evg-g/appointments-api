"""Idempotency-Key on appointment creation, exercised through the router.

Confirms the router behaviours: a repeated key replays the original 201 (no duplicate row), and a
key whose first attempt failed is released so a corrected retry succeeds.
"""

from __future__ import annotations

from typing import Any

import httpx

from tests.integration.helpers import auth_header

_STARTS_AT = "2035-06-10T10:00:00+00:00"  # inside booking_env's 06:00-22:00 UTC hours


def _body(env: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body = {
        "clinic_id": str(env["clinic_id"]),
        "clinician_id": str(env["clinician_id"]),
        "service_id": str(env["service_id"]),
        "starts_at": _STARTS_AT,
    }
    body.update(overrides)
    return body


async def test_same_key_replays_the_original_response(
    client: httpx.AsyncClient, booking_env: dict[str, Any]
) -> None:
    headers = {**auth_header(booking_env["patient_token"]), "Idempotency-Key": "book-k1"}

    first = await client.post("/api/v1/appointments", json=_body(booking_env), headers=headers)
    assert first.status_code == 201, first.text

    second = await client.post("/api/v1/appointments", json=_body(booking_env), headers=headers)
    assert second.status_code == 201
    assert second.headers.get("idempotency-replayed") == "true"
    assert second.json()["id"] == first.json()["id"]

    # Exactly one appointment exists.
    listing = await client.get(
        "/api/v1/appointments", headers=auth_header(booking_env["admin_token"])
    )
    assert len(listing.json()["data"]) == 1


async def test_key_is_released_after_a_failed_attempt(
    client: httpx.AsyncClient, booking_env: dict[str, Any]
) -> None:
    headers = {**auth_header(booking_env["patient_token"]), "Idempotency-Key": "book-k2"}

    # First attempt fails (unknown clinic) -> the key must be released, not stored.
    failed = await client.post(
        "/api/v1/appointments",
        json=_body(booking_env, clinic_id="00000000-0000-4000-8000-000000000000"),
        headers=headers,
    )
    assert failed.status_code == 404

    # Retry with the same key and a valid body succeeds.
    ok = await client.post("/api/v1/appointments", json=_body(booking_env), headers=headers)
    assert ok.status_code == 201, ok.text
    assert ok.headers.get("idempotency-replayed") is None
