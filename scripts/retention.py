#!/usr/bin/env python
"""Telemetry retention job — prune raw readings past the retention window.

The design keeps telemetry in plain PostgreSQL with no TimescaleDB (spec §5). Two halves:

* **Downsampling is on-read**: the time-series endpoint aggregates with ``date_bin`` into whatever
  bucket the caller asks for, so there is no second rollup table to keep in sync.
* **Retention is this job**: raw readings older than the window are deleted, keeping the
  append-heavy ``telemetry_readings`` table (and its BRIN index) bounded. Excursions are derived and
  stored separately, so pruning raw readings never loses a recorded breach.

Run it on a schedule (cron / a Kubernetes CronJob / an Azure Container Apps job):

    uv run python scripts/retention.py --retention-days 90
    uv run python scripts/retention.py --retention-days 90 --dry-run

The window defaults to ``TELEMETRY_RETENTION_DAYS`` (or 90). Deletion is chunked so a large backlog
does not lock the table in one long transaction.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from appointments_api.config import get_settings
from appointments_api.db import create_engine

_DEFAULT_DAYS = int(os.environ.get("TELEMETRY_RETENTION_DAYS", "90"))


async def _delete_chunked(retention_days: int, *, dry_run: bool, chunk_size: int) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    deleted_total = 0
    try:
        async with engine.begin() as conn:
            count = await conn.scalar(
                text("SELECT count(*) FROM telemetry_readings WHERE measured_at < :cutoff"),
                {"cutoff": cutoff},
            )
        to_delete = int(count or 0)
        print(f"{to_delete} readings older than {cutoff.isoformat()} ({retention_days}d).")
        if dry_run or to_delete == 0:
            return 0

        while True:
            async with engine.begin() as conn:
                result = await conn.execute(
                    text(
                        "DELETE FROM telemetry_readings WHERE id IN ("
                        "  SELECT id FROM telemetry_readings"
                        "  WHERE measured_at < :cutoff LIMIT :chunk"
                        ")"
                    ),
                    {"cutoff": cutoff, "chunk": chunk_size},
                )
            if result.rowcount == 0:
                break
            deleted_total += result.rowcount
        print(f"deleted {deleted_total} readings.")
    finally:
        await engine.dispose()
    return deleted_total


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune telemetry readings past the retention.")
    parser.add_argument("--retention-days", type=int, default=_DEFAULT_DAYS)
    parser.add_argument("--chunk-size", type=int, default=10_000)
    parser.add_argument("--dry-run", action="store_true", help="Report the count; delete nothing.")
    args = parser.parse_args()
    asyncio.run(
        _delete_chunked(args.retention_days, dry_run=args.dry_run, chunk_size=args.chunk_size)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
