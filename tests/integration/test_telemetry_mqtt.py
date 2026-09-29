"""Integration tests for the MQTT ingestion path (real Postgres + Redis, no broker).

The broker loop and the message handler are separate on purpose: ``handle_message`` takes a topic
and a raw payload, so it can be driven directly here — proving MQTT messages funnel into the *same*
ingestion service as the HTTP endpoint (spec §5), without standing up Mosquitto. A dedicated
broker-backed SIL test lives in the device repo (milestone 11).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from appointments_api.services.clock import SystemClock
from appointments_api.workers.telemetry_mqtt import device_id_from_topic, handle_message

API = "/api/v1"


async def _provision(client: httpx.AsyncClient, admin: str, clinic_id: uuid.UUID) -> str:
    resp = await client.post(
        f"{API}/devices",
        headers={"Authorization": f"Bearer {admin}"},
        json={
            "clinic_id": str(clinic_id),
            "location_label": "Fridge B",
            "hardware_version": "hw",
            "firmware_version": "fw",
        },
    )
    assert resp.status_code == 201, resp.text
    device_id: str = resp.json()["device"]["id"]
    return device_id


def _topic(clinic_id: uuid.UUID, device_id: str) -> str:
    return f"aurora/v1/clinic/{clinic_id}/device/{device_id}/telemetry"


def _envelope(device_id: str, readings: list[tuple[int, datetime, float]]) -> dict[str, Any]:
    return {
        "v": 2,
        "device_id": device_id,
        "idempotency_key": "mqtt-key",
        "readings": [
            {
                "sequence": seq,
                "reading": {
                    "v": 2,
                    "measured_at": at.isoformat(),
                    "temperature_c": temp,
                    "humidity_pct": 40.0,
                    "battery_pct": 90.0,
                },
            }
            for seq, at, temp in readings
        ],
    }


def test_device_id_from_topic() -> None:
    did = str(uuid.uuid4())
    cid = str(uuid.uuid4())
    assert device_id_from_topic(f"aurora/v1/clinic/{cid}/device/{did}/telemetry") == did
    assert device_id_from_topic("aurora/v1/clinic/x/device/y/health") is None
    assert device_id_from_topic("garbage") is None


async def test_mqtt_message_ingested_like_http(seed, login, client, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
    await seed.user(role="PLATFORM_ADMIN", email="madmin@x.io")
    admin = await login("madmin@x.io")
    clinic_id = await seed.clinic()
    device_id = await _provision(client, admin, clinic_id)
    now = datetime.now(UTC)

    payload = json.dumps(_envelope(device_id, [(1, now - timedelta(seconds=30), 6.0)]))
    await handle_message(
        _topic(clinic_id, device_id),
        payload,
        sessionmaker=app.state.sessionmaker,
        redis=app.state.redis,
        settings=app.state.settings,
        clock=SystemClock(),
    )

    # The reading landed and is visible through the read API — same store as the HTTP path.
    health = await client.get(
        f"{API}/devices/{device_id}/health", headers={"Authorization": f"Bearer {admin}"}
    )
    assert health.status_code == 200
    assert health.json()["last_temperature_c"] == pytest.approx(6.0)
    assert health.json()["status"] == "ACTIVE"


async def test_mqtt_contract_violation_dropped(seed, login, client, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
    await seed.user(role="PLATFORM_ADMIN", email="madmin2@x.io")
    admin = await login("madmin2@x.io")
    clinic_id = await seed.clinic()
    device_id = await _provision(client, admin, clinic_id)

    # Missing temperature_c → contract violation. Must be dropped, not raised, and nothing stored.
    bad = {
        "v": 2,
        "device_id": device_id,
        "idempotency_key": "k",
        "readings": [
            {"sequence": 1, "reading": {"v": 2, "measured_at": "2026-01-01T00:00:00+00:00"}}
        ],
    }
    await handle_message(
        _topic(clinic_id, device_id),
        json.dumps(bad),
        sessionmaker=app.state.sessionmaker,
        redis=app.state.redis,
        settings=app.state.settings,
        clock=SystemClock(),
    )

    health = await client.get(
        f"{API}/devices/{device_id}/health", headers={"Authorization": f"Bearer {admin}"}
    )
    assert health.json()["last_temperature_c"] is None  # nothing ingested


async def test_mqtt_unknown_device_dropped(app: FastAPI) -> None:
    # A device id that was never provisioned: DeviceNotFoundError is swallowed, not raised.
    unknown = str(uuid.uuid4())
    clinic_id = uuid.uuid4()
    payload = json.dumps(_envelope(unknown, [(1, datetime.now(UTC), 5.0)]))
    await handle_message(
        _topic(clinic_id, unknown),
        payload,
        sessionmaker=app.state.sessionmaker,
        redis=app.state.redis,
        settings=app.state.settings,
        clock=SystemClock(),
    )  # must not raise
