"""Unit tests for the idempotency service.

Technique on show: **fake Redis**. We run the real ``RedisIdempotencyStore`` and the real
``IdempotencyService`` against an in-memory ``fakeredis`` client — no container, no network — so
the exact SET NX / GET / DELETE logic is exercised. The integration tier repeats the same flow
against the real Redis container through the HTTP API.
"""

from __future__ import annotations

import uuid

import fakeredis.aioredis
import pytest

from appointments_api.repositories.redis_idempotency import RedisIdempotencyStore
from appointments_api.services.idempotency import (
    BeginState,
    IdempotencyService,
    StoredResponse,
    request_fingerprint,
)

SCOPE = "appointments:create"
KEY = "key-123"
FP = "fingerprint-a"


@pytest.fixture
def service() -> IdempotencyService:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return IdempotencyService(RedisIdempotencyStore(redis), ttl_seconds=60)


async def test_first_call_is_new(service: IdempotencyService) -> None:
    result = await service.begin(SCOPE, KEY, FP)
    assert result.state is BeginState.NEW


async def test_second_call_before_completion_is_in_progress(service: IdempotencyService) -> None:
    await service.begin(SCOPE, KEY, FP)
    again = await service.begin(SCOPE, KEY, FP)
    assert again.state is BeginState.IN_PROGRESS


async def test_completed_key_replays_stored_response(service: IdempotencyService) -> None:
    await service.begin(SCOPE, KEY, FP)
    stored = StoredResponse(status_code=201, body={"id": "abc", "status": "REQUESTED"}, etag='"1"')
    await service.complete(SCOPE, KEY, FP, stored)

    replay = await service.begin(SCOPE, KEY, FP)
    assert replay.state is BeginState.REPLAY
    assert replay.stored == stored


async def test_same_key_different_body_is_a_mismatch(service: IdempotencyService) -> None:
    await service.begin(SCOPE, KEY, FP)
    other = await service.begin(SCOPE, KEY, "fingerprint-b")
    assert other.state is BeginState.FINGERPRINT_MISMATCH


async def test_mismatch_detected_even_after_completion(service: IdempotencyService) -> None:
    await service.begin(SCOPE, KEY, FP)
    await service.complete(SCOPE, KEY, FP, StoredResponse(201, {"id": "abc"}, '"1"'))
    other = await service.begin(SCOPE, KEY, "fingerprint-b")
    assert other.state is BeginState.FINGERPRINT_MISMATCH


async def test_release_lets_a_retry_start_fresh(service: IdempotencyService) -> None:
    await service.begin(SCOPE, KEY, FP)  # NEW → pending marker set
    await service.release(SCOPE, KEY, FP)  # work failed → drop the marker
    retry = await service.begin(SCOPE, KEY, FP)
    assert retry.state is BeginState.NEW


async def test_release_does_not_remove_a_finished_record(service: IdempotencyService) -> None:
    await service.begin(SCOPE, KEY, FP)
    await service.complete(SCOPE, KEY, FP, StoredResponse(201, {"id": "abc"}, '"1"'))
    # A late release from a different (failed) attempt must not wipe the stored success.
    await service.release(SCOPE, KEY, FP)
    replay = await service.begin(SCOPE, KEY, FP)
    assert replay.state is BeginState.REPLAY


def test_fingerprint_is_stable_and_sensitive_to_body_and_principal() -> None:
    principal = uuid.uuid4()
    other_principal = uuid.uuid4()
    body = b'{"a": 1}'

    assert request_fingerprint(principal, body) == request_fingerprint(principal, body)
    assert request_fingerprint(principal, body) != request_fingerprint(principal, b'{"a": 2}')
    assert request_fingerprint(principal, body) != request_fingerprint(other_principal, body)
