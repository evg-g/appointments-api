"""Live telemetry fan-out backed by a Redis Stream.

A Redis Stream is the natural fit for Server-Sent Events: every entry has a monotonic id, which is
exactly what the SSE ``id:`` field and the client's ``Last-Event-ID`` reconnect header need. On
publish we ``XADD`` (capped with ``MAXLEN`` so the stream never grows unbounded); on subscribe we
optionally ``XRANGE`` the entries a reconnecting client missed, then ``XREAD BLOCK`` to follow new
ones. The stream id doubles as the SSE event id, so resumption is a one-liner on the client and a
range query here — no per-connection state to keep on the server.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from redis.asyncio import Redis

_STREAM_KEY = "telemetry:stream"
_DEFAULT_MAXLEN = 10_000


class RedisTelemetryStream:
    def __init__(
        self, redis: Redis, *, key: str = _STREAM_KEY, maxlen: int = _DEFAULT_MAXLEN
    ) -> None:
        self._redis = redis
        self._key = key
        self._maxlen = maxlen

    async def publish(self, event: dict[str, object]) -> None:
        """Append one reading to the stream. Satisfies the ingestion ``TelemetryPublisher`` port."""
        await self._redis.xadd(
            self._key, {"data": json.dumps(event)}, maxlen=self._maxlen, approximate=True
        )

    async def replay_since(self, last_id: str) -> list[tuple[str, str]]:
        """Entries added strictly after ``last_id`` (what a reconnecting client missed)."""
        try:
            entries = await self._redis.xrange(self._key, min=f"({last_id}", max="+")
        except Exception:
            # A malformed Last-Event-ID: treat as "no history", fall through to live follow.
            return []
        return [(entry_id, fields["data"]) for entry_id, fields in entries]

    async def follow(
        self, from_id: str, *, block_ms: int = 15_000
    ) -> AsyncIterator[tuple[str, str] | None]:
        """Yield ``(id, data)`` for each new entry; yield ``None`` on a block timeout as a heartbeat
        so the SSE endpoint can emit a keep-alive comment and notice client disconnects."""
        cursor = from_id or "$"
        while True:
            resp = await self._redis.xread({self._key: cursor}, block=block_ms, count=100)
            if not resp:
                yield None
                continue
            for _stream_key, entries in resp:
                for entry_id, fields in entries:
                    cursor = entry_id
                    yield entry_id, fields["data"]
