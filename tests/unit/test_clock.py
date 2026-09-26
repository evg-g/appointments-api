"""Unit test for the production clock.

Reading the OS clock is not I/O in the database/network sense the unit tier forbids; we only
assert the shape of what it returns (timezone-aware UTC), never a specific instant.
"""

from __future__ import annotations

from datetime import UTC, datetime

from appointments_api.services.clock import SystemClock


def test_system_clock_returns_timezone_aware_utc() -> None:
    before = datetime.now(UTC)
    now = SystemClock().now()
    after = datetime.now(UTC)

    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(None)  # zero offset: it is UTC
    assert before <= now <= after
