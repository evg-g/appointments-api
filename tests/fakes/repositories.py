"""In-memory repository fakes implementing the service-layer protocols."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from appointments_api.services.availability import TimeInterval


class InMemoryBusyRepository:
    """A fake :class:`BusyPeriodRepository` backed by a plain list of intervals.

    It filters to the requested window exactly as a SQL query would, so a service that works
    against this fake works against the real repository too.
    """

    def __init__(self, intervals: dict[str, list[TimeInterval]] | None = None) -> None:
        self._intervals: dict[str, list[TimeInterval]] = intervals or {}

    def add(self, clinician_id: str, interval: TimeInterval) -> None:
        self._intervals.setdefault(clinician_id, []).append(interval)

    async def busy_intervals(
        self, clinician_id: str, start: datetime, end: datetime
    ) -> Sequence[TimeInterval]:
        return [i for i in self._intervals.get(clinician_id, []) if i.start < end and start < i.end]
