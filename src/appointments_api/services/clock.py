"""A clock behind a Protocol so time is injectable.

Business logic never calls ``datetime.now()`` directly. It asks an injected ``Clock``. In
production that is ``SystemClock``; in tests it is a fake that returns a fixed instant, which is
what makes DST math and cancellation-window rules testable without waiting for real time to pass.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    """The real clock: the operating system's UTC time."""

    def now(self) -> datetime:
        return datetime.now(UTC)
