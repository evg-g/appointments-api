"""A fake clock that returns a fixed instant, so time-dependent logic is deterministic."""

from __future__ import annotations

from datetime import datetime

from appointments_api.services.clock import Clock


class FixedClock(Clock):
    """Returns the same UTC instant every call. Set ``instant`` to move time in a test."""

    def __init__(self, instant: datetime) -> None:
        self.instant = instant

    def now(self) -> datetime:
        return self.instant
