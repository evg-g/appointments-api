"""Integration tests for telemetry ingestion over the HTTP path (real Postgres + Redis).

These exercise the whole vertical slice against real infrastructure: provisioning + credentials,
idempotent bulk ingestion, out-of-order backfill re-raising an excursion, clock-skew handling,
server-side downsampling, device health, and excursion acknowledgement. The MQTT path and the SSE
stream have their own files (``test_telemetry_mqtt``, ``test_telemetry_sse``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

API = "/api/v1"


async def _admin_token(seed: Any, login: Any) -> str:
    await seed.user(role="PLATFORM_ADMIN", email="tadmin@x.io")
    return await login("tadmin@x.io")  # type: ignore[no-any-return]


async def _provision(
    client: httpx.AsyncClient, admin_token: str, clinic_id: uuid.UUID
) -> tuple[str, str]:
    resp = await client.post(
        f"{API}/devices",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "clinic_id": str(clinic_id),
            "location_label": "Vaccine fridge A",
            "hardware_version": "rpi-4b",
            "firmware_version": "1.2.3",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["device"]["id"], body["secret"]


def _envelope(readings: list[tuple[int, datetime, float]], *, v: int = 2) -> dict[str, Any]:
    return {
        "v": v,
        "device_id": "ignored-on-http",
        "idempotency_key": "k",
        "readings": [
            {
                "sequence": seq,
                "reading": {
                    "v": v,
                    "measured_at": at.isoformat(),
                    "temperature_c": temp,
                    "humidity_pct": 45.0,
                },
            }
            for seq, at, temp in readings
        ],
    }


async def _post_batch(
    client: httpx.AsyncClient, device_id: str, secret: str, envelope: dict[str, Any]
) -> httpx.Response:
    return await client.post(
        f"{API}/devices/{device_id}/telemetry:batch",
        headers={"X-Device-Secret": secret},
        json=envelope,
    )


async def test_batch_ingest_and_idempotent_replay(seed, login, client) -> None:  # type: ignore[no-untyped-def]
    admin = await _admin_token(seed, login)
    clinic_id = await seed.clinic()
    device_id, secret = await _provision(client, admin, clinic_id)

    now = datetime.now(UTC)
    envelope = _envelope(
        [(1, now - timedelta(seconds=120), 5.0), (2, now - timedelta(seconds=60), 5.1)]
    )

    first = await _post_batch(client, device_id, secret, envelope)
    assert first.status_code == 200, first.text
    assert first.json()["accepted"] == 2
    assert first.json()["duplicates"] == 0

    # Replay the exact same batch: idempotent, nothing stored twice.
    second = await _post_batch(client, device_id, secret, envelope)
    assert second.status_code == 200
    assert second.json()["accepted"] == 0
    assert second.json()["duplicates"] == 2


async def test_missing_or_bad_device_secret_rejected(seed, login, client) -> None:  # type: ignore[no-untyped-def]
    admin = await _admin_token(seed, login)
    clinic_id = await seed.clinic()
    device_id, secret = await _provision(client, admin, clinic_id)
    now = datetime.now(UTC)
    envelope = _envelope([(1, now, 5.0)])

    no_secret = await client.post(f"{API}/devices/{device_id}/telemetry:batch", json=envelope)
    assert no_secret.status_code == 401

    wrong = await _post_batch(client, device_id, "wrong-secret", envelope)
    assert wrong.status_code == 401


async def test_credential_rotation_invalidates_old_secret(seed, login, client) -> None:  # type: ignore[no-untyped-def]
    admin = await _admin_token(seed, login)
    clinic_id = await seed.clinic()
    device_id, old_secret = await _provision(client, admin, clinic_id)

    rot = await client.post(
        f"{API}/devices/{device_id}/credentials:rotate",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert rot.status_code == 200
    new_secret = rot.json()["secret"]
    assert new_secret != old_secret

    now = datetime.now(UTC)
    envelope = _envelope([(1, now, 5.0)])
    assert (await _post_batch(client, device_id, old_secret, envelope)).status_code == 401
    assert (await _post_batch(client, device_id, new_secret, envelope)).status_code == 200


async def test_future_reading_rejected_skew_flagged(seed, login, client) -> None:  # type: ignore[no-untyped-def]
    admin = await _admin_token(seed, login)
    clinic_id = await seed.clinic()
    device_id, secret = await _provision(client, admin, clinic_id)
    now = datetime.now(UTC)

    envelope = _envelope(
        [
            (1, now + timedelta(minutes=10), 5.0),  # future → rejected
            (2, now - timedelta(minutes=20), 5.0),  # 20 min skew → flagged, still stored
            (3, now - timedelta(seconds=30), 5.0),  # fresh → accepted, not flagged
        ]
    )
    resp = await _post_batch(client, device_id, secret, envelope)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    results = {r["sequence"]: r for r in body["results"]}
    assert results[1]["status"] == "rejected"
    assert "future" in results[1]["detail"]
    assert results[2]["status"] == "accepted"
    assert "skew" in (results[2]["detail"] or "")
    assert results[3]["status"] == "accepted"
    assert body["rejected"] == 1
    assert body["accepted"] == 2


async def test_out_of_order_backfill_raises_excursion(seed, login, client) -> None:  # type: ignore[no-untyped-def]
    admin = await _admin_token(seed, login)
    clinic_id = await seed.clinic()
    device_id, secret = await _provision(client, admin, clinic_id)

    # A device-scoped band matching the shared excursion fixtures.
    pol = await client.post(
        f"{API}/threshold-policies",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "device_id": device_id,
            "min_temperature_c": 2.0,
            "max_temperature_c": 8.0,
            "dwell_minutes": 15.0,
            "recovery_minutes": 10.0,
        },
    )
    assert pol.status_code == 201, pol.text

    base = datetime.now(UTC) - timedelta(hours=2)

    def at(off: int) -> datetime:
        return base + timedelta(seconds=off)

    # Sparse first: the middle of a sustained-high breach is missing, so no dwell is observed yet.
    sparse = _envelope(
        [(1, at(0), 5.0), (7, at(1800), 5.0), (8, at(2100), 5.0), (9, at(2400), 5.0)]
    )
    r1 = await _post_batch(client, device_id, secret, sparse)
    assert r1.status_code == 200
    assert r1.json()["open_excursions"] == 0

    # Backfill the missing high readings, out of order. Now the excursion is derivable and closed.
    backfill = _envelope(
        [
            (2, at(300), 5.0),
            (6, at(1500), 10.0),
            (3, at(600), 10.0),
            (5, at(1200), 10.0),
            (4, at(900), 10.0),
        ]
    )
    r2 = await _post_batch(client, device_id, secret, backfill)
    assert r2.status_code == 200, r2.text

    listing = await client.get(
        f"{API}/devices/{device_id}/excursions",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert listing.status_code == 200
    data = listing.json()["data"]
    assert len(data) == 1
    assert data[0]["direction"] == "high"
    assert data[0]["ended_at"] is not None
    assert data[0]["peak_temperature_c"] == pytest.approx(10.0)

    # Acknowledge it.
    exc_id = data[0]["id"]
    ack = await client.post(
        f"{API}/excursions/{exc_id}:acknowledge",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert ack.status_code == 200
    assert ack.json()["acknowledged_by"] is not None
    assert ack.json()["acknowledged_at"] is not None


async def test_time_series_downsampling(seed, login, client) -> None:  # type: ignore[no-untyped-def]
    admin = await _admin_token(seed, login)
    clinic_id = await seed.clinic()
    device_id, secret = await _provision(client, admin, clinic_id)
    now = datetime.now(UTC)

    # Six fresh readings across ~5 minutes (all within skew so none flagged).
    readings = [(i, now - timedelta(seconds=290 - i * 50), 4.0 + i * 0.2) for i in range(6)]
    resp = await _post_batch(client, device_id, secret, _envelope(readings))
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 6

    ts = await client.get(
        f"{API}/devices/{device_id}/telemetry",
        headers={"Authorization": f"Bearer {admin}"},
        params={"bucket": "1m", "agg": "avg"},
    )
    assert ts.status_code == 200, ts.text
    body = ts.json()
    assert body["bucket_seconds"] == 60
    assert body["agg"] == "avg"
    assert body["points"], "expected at least one bucket"
    assert sum(p["sample_count"] for p in body["points"]) == 6

    ts_max = await client.get(
        f"{API}/devices/{device_id}/telemetry",
        headers={"Authorization": f"Bearer {admin}"},
        params={"bucket": "10m", "agg": "max"},
    )
    assert ts_max.status_code == 200
    points = ts_max.json()["points"]
    # Buckets align to a fixed epoch, so the six readings may land in one or two 10-minute buckets
    # depending on the wall clock; assert on the aggregate, not the bucket count.
    assert sum(p["sample_count"] for p in points) == 6
    assert max(p["value"] for p in points) == pytest.approx(5.0)  # 4.0 + 5*0.2


async def test_device_health_reflects_last_reading(seed, login, client) -> None:  # type: ignore[no-untyped-def]
    admin = await _admin_token(seed, login)
    clinic_id = await seed.clinic()
    device_id, secret = await _provision(client, admin, clinic_id)
    now = datetime.now(UTC)

    await _post_batch(client, device_id, secret, _envelope([(1, now - timedelta(seconds=30), 6.5)]))

    health = await client.get(
        f"{API}/devices/{device_id}/health",
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert health.status_code == 200, health.text
    body = health.json()
    assert body["status"] == "ACTIVE"  # promoted from PROVISIONED on first report
    assert body["last_seen_at"] is not None
    assert body["last_temperature_c"] == pytest.approx(6.5)
    assert body["open_excursions"] == 0


async def test_previous_schema_version_accepted(seed, login, client) -> None:  # type: ignore[no-untyped-def]
    admin = await _admin_token(seed, login)
    clinic_id = await seed.clinic()
    device_id, secret = await _provision(client, admin, clinic_id)
    now = datetime.now(UTC)

    # v1 payload (no battery) — the previous version, must still be accepted (spec §8 rule 5).
    envelope = _envelope([(1, now - timedelta(seconds=30), 5.0)], v=1)
    resp = await _post_batch(client, device_id, secret, envelope)
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1


async def test_provisioning_requires_admin(seed, login, client) -> None:  # type: ignore[no-untyped-def]
    patient_id = await seed.user(role="PATIENT", email="pat@x.io")
    assert patient_id is not None
    clinic_id = await seed.clinic()
    patient_token = await login("pat@x.io")

    body = {
        "clinic_id": str(clinic_id),
        "location_label": "L",
        "hardware_version": "h",
        "firmware_version": "f",
    }
    # No token → 401.
    assert (await client.post(f"{API}/devices", json=body)).status_code == 401
    # Patient token → 403.
    forbidden = await client.post(
        f"{API}/devices", headers={"Authorization": f"Bearer {patient_token}"}, json=body
    )
    assert forbidden.status_code == 403
