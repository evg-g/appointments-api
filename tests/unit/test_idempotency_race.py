"""The idempotency marker-expiry race in ``IdempotencyService.begin``.

If a claim (``put_if_absent``) loses but the marker has since expired (``get`` returns ``None``),
the service must recover by trying to claim once more, and if even that loses, proceed anyway (worst
case two idempotent attempts run). This TTL race cannot be provoked deterministically over HTTP, so
it is exercised here with a scripted store.
"""

from __future__ import annotations

import uuid

from appointments_api.services.idempotency import (
    BeginState,
    IdempotencyService,
    request_fingerprint,
)


class ScriptedStore:
    """An ``IdempotencyKeyStore`` whose ``put_if_absent``/``get`` return canned, ordered results."""

    def __init__(self, *, put_results: list[bool], get_results: list[str | None]) -> None:
        self._put = list(put_results)
        self._get = list(get_results)

    async def put_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        return self._put.pop(0)

    async def get(self, key: str) -> str | None:
        return self._get.pop(0)

    async def put(self, key: str, value: str, ttl_seconds: int) -> None:  # pragma: no cover
        raise AssertionError("not used in these tests")

    async def delete_if_matches(self, key: str, value: str) -> None:  # pragma: no cover
        raise AssertionError("not used in these tests")


def _fingerprint() -> str:
    return request_fingerprint(uuid.uuid4(), b"{}")


async def test_reclaim_after_marker_expired_succeeds() -> None:
    # First claim loses, marker is gone, second claim wins.
    store = ScriptedStore(put_results=[False, True], get_results=[None])
    service = IdempotencyService(store, ttl_seconds=60)

    result = await service.begin("appointments:create", "k", _fingerprint())

    assert result.state is BeginState.NEW


async def test_reclaim_loses_but_marker_still_gone_proceeds() -> None:
    # First claim loses, marker gone, second claim also loses, but the marker is *still* gone —
    # proceed as NEW rather than block.
    store = ScriptedStore(put_results=[False, False], get_results=[None, None])
    service = IdempotencyService(store, ttl_seconds=60)

    result = await service.begin("appointments:create", "k", _fingerprint())

    assert result.state is BeginState.NEW
