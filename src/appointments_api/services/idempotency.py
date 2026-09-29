"""Idempotency-Key handling for unsafe requests (spec §4 rule 6).

A client that creates a resource may retry after a network blip without knowing whether the first
attempt reached us. If it sends the same ``Idempotency-Key`` on the retry, we must return the
*original* result rather than create a second row.

The mechanics, stored under one Redis key per ``(scope, key)``:

1. **begin** — atomically claim the key with a *pending* marker (``SET NX``). The first caller wins
   and proceeds; a concurrent caller with the same key sees the marker and is told the request is
   in progress. A caller whose request body differs from the stored one is told the key was reused.
2. **complete** — once the work succeeds, overwrite the marker with the *finished* response
   (status, body, ETag). Later retries replay it verbatim.
3. **release** — if the work fails, drop the pending marker so a genuine retry can start fresh; a
   failed attempt must not poison the key.

Storage sits behind a small Protocol so unit tests run it over ``fakeredis`` and integration tests
over the real Redis container — the orchestration logic is identical either way.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class StoredResponse:
    """The finished response we replay for a repeated key."""

    status_code: int
    body: dict[str, Any]
    etag: str | None = None


class IdempotencyKeyStore(Protocol):
    """Minimal key/value operations, each with a TTL, that the service composes into the protocol
    above. Kept deliberately small so any backend (Redis, fake) is trivial to provide."""

    async def put_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool: ...
    async def get(self, key: str) -> str | None: ...
    async def put(self, key: str, value: str, ttl_seconds: int) -> None: ...
    async def delete_if_matches(self, key: str, value: str) -> None: ...


class BeginState(Enum):
    NEW = auto()  # first time we have seen this key: proceed with the work
    IN_PROGRESS = auto()  # a concurrent request with the same key is still running
    REPLAY = auto()  # the work already finished: return the stored response
    FINGERPRINT_MISMATCH = auto()  # the key was reused with a different request body


@dataclass(frozen=True, slots=True)
class BeginResult:
    state: BeginState
    stored: StoredResponse | None = None


def request_fingerprint(principal: uuid.UUID, body: bytes) -> str:
    """A stable hash of who is calling and the exact bytes they sent.

    Binding the key to the body is what lets us detect a client that accidentally reuses a key for
    a different request — a common and dangerous mistake — instead of returning the wrong resource.
    """
    digest = hashlib.sha256()
    digest.update(principal.bytes)
    digest.update(b"\x00")
    digest.update(body)
    return digest.hexdigest()


class IdempotencyService:
    def __init__(self, store: IdempotencyKeyStore, *, ttl_seconds: int) -> None:
        self._store = store
        self._ttl = ttl_seconds

    @staticmethod
    def _redis_key(scope: str, key: str) -> str:
        return f"idem:{scope}:{key}"

    @staticmethod
    def _pending_value(fingerprint: str) -> str:
        return json.dumps({"state": "pending", "fp": fingerprint})

    async def begin(self, scope: str, key: str, fingerprint: str) -> BeginResult:
        redis_key = self._redis_key(scope, key)
        pending = self._pending_value(fingerprint)

        if await self._store.put_if_absent(redis_key, pending, self._ttl):
            return BeginResult(BeginState.NEW)

        raw = await self._store.get(redis_key)
        if raw is None:
            # The marker expired between the failed claim and this read. Try to claim it once more;
            # if that also loses, just proceed (worst case two attempts run, both idempotent).
            if await self._store.put_if_absent(redis_key, pending, self._ttl):
                return BeginResult(BeginState.NEW)
            raw = await self._store.get(redis_key)
            if raw is None:
                return BeginResult(BeginState.NEW)

        data = json.loads(raw)
        if data.get("fp") != fingerprint:
            return BeginResult(BeginState.FINGERPRINT_MISMATCH)
        if data.get("state") == "done":
            response = data["response"]
            return BeginResult(
                BeginState.REPLAY,
                StoredResponse(
                    status_code=response["status_code"],
                    body=response["body"],
                    etag=response.get("etag"),
                ),
            )
        return BeginResult(BeginState.IN_PROGRESS)

    async def complete(
        self, scope: str, key: str, fingerprint: str, response: StoredResponse
    ) -> None:
        value = json.dumps(
            {
                "state": "done",
                "fp": fingerprint,
                "response": {
                    "status_code": response.status_code,
                    "body": response.body,
                    "etag": response.etag,
                },
            }
        )
        await self._store.put(self._redis_key(scope, key), value, self._ttl)

    async def release(self, scope: str, key: str, fingerprint: str) -> None:
        # Only remove the marker if it is still *our* pending marker: never delete a finished
        # record or another request's claim.
        await self._store.delete_if_matches(
            self._redis_key(scope, key), self._pending_value(fingerprint)
        )
