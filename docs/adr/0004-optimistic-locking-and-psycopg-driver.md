# 4. Optimistic locking with a version column, and one psycopg driver

- Status: accepted
- Date: 2026-09-26

## Context

Two smaller decisions from the domain-core milestone, recorded together.

**Concurrent edits to one appointment.** Two admins open the same appointment and both save. The
last write silently wins and the first person's change vanishes.

**Database driver.** The async app needs an async driver; Alembic migrations run synchronously.
Using two different drivers (e.g. asyncpg for the app, psycopg2 for Alembic) means two dependency
chains and two dialects to reason about.

## Decision

- **Optimistic locking via a `version` column.** `Appointment` has an integer `version` wired as
  SQLAlchemy's `version_id_col`. Every UPDATE includes `WHERE version = <the value we read>` and
  bumps it. If the row moved since we read it, zero rows match and SQLAlchemy raises
  `StaleDataError`. This is the database-level backing for the `If-Match`/ETag behaviour added in
  milestone 4 — a stale write becomes a loud `412`, not a lost update.
- **One driver: psycopg 3.** `postgresql+psycopg://` works in both async (the app, milestone 3)
  and sync (Alembic) modes, so the whole repo has a single driver and a single dialect.

## Consequences

- Lost updates become explicit errors the API can turn into a `412`.
- Only one database driver to install, pin, and secure.
- `version` is managed by the mapper; application code never sets it by hand.
