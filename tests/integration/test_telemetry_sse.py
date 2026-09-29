"""Integration tests for the live telemetry SSE stream (real Redis).

The substance of spec §5's "reconnect and Last-Event-ID resumption" is tested by driving the
endpoint's event generator directly against a real Redis Stream: it is deterministic (no blocking
waits, no racing a background publisher) and it proves the two things that matter — a reconnecting
client replays what it missed, and resuming from an id continues *after* it rather than from the
start. A separate check proves the HTTP endpoint itself requires authentication.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import FastAPI

from appointments_api.api.routers.streams import _event_source
from appointments_api.repositories.redis_telemetry_stream import RedisTelemetryStream

API = "/api/v1"


class _FakeRequest:
    async def is_disconnected(self) -> bool:
        return False


def _parse(chunk: str) -> tuple[str | None, dict[str, Any] | None]:
    """Parse one SSE chunk into (id, data). Heartbeat comments return (None, None)."""
    event_id: str | None = None
    data: dict[str, Any] | None = None
    for line in chunk.splitlines():
        if line.startswith("id: "):
            event_id = line[4:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
    return event_id, data


async def _collect(gen: Any, n: int) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    async for chunk in gen:
        event_id, data = _parse(chunk)
        if event_id is None or data is None:
            continue  # heartbeat
        events.append((event_id, data))
        if len(events) >= n:
            break
    return events


async def test_sse_replays_and_resumes_from_last_event_id(app: FastAPI) -> None:
    stream = RedisTelemetryStream(app.state.redis, key="telemetry:stream:test")
    await stream.publish({"device_id": "d", "sequence": 1, "temperature_c": 5.0})
    await stream.publish({"device_id": "d", "sequence": 2, "temperature_c": 6.0})

    # A reconnecting client with Last-Event-ID "0" replays everything it missed.
    replayed = await _collect(_event_source(_FakeRequest(), stream, "0"), 2)  # type: ignore[arg-type]
    assert [d["sequence"] for _id, d in replayed] == [1, 2]
    id_of_second = replayed[1][0]

    # Resuming from the id of the second event must continue AFTER it, not from the start.
    await stream.publish({"device_id": "d", "sequence": 3, "temperature_c": 7.0})
    resumed = await _collect(_event_source(_FakeRequest(), stream, id_of_second), 1)  # type: ignore[arg-type]
    assert len(resumed) == 1
    assert resumed[0][1]["sequence"] == 3  # got #3, not a replay of #1/#2


async def test_sse_endpoint_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"{API}/streams/telemetry")
    assert resp.status_code == 401
