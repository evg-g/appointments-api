# 2. Prevent double-booking with a Postgres exclusion constraint

- Status: accepted
- Date: 2026-09-26

## Context

Two appointments must never overlap for the same clinician. If we enforce this only in
application code ("SELECT to check, then INSERT"), two requests that run at the same time can both
pass the check before either inserts — a classic race that produces a double-booking. The window
is small, but it is real, and it is exactly the kind of bug that never shows up in a demo and
always shows up in production.

## Decision

Enforce the rule in the database with a PostgreSQL **exclusion constraint**:

```sql
EXCLUDE USING gist (
    clinician_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
) WHERE (status NOT IN ('CANCELLED', 'NO_SHOW'))
```

Read it as: reject any new row that has the *same* `clinician_id` (`WITH =`) *and* an
*overlapping* time range (`WITH &&`) as an existing row. The `WHERE` clause means cancelled and
no-show appointments do not hold a slot. This needs the `btree_gist` extension so an equality
column and a range can live in one GiST index; the migration creates it.

We still check availability in the service layer for a friendly error, but the database is the
source of truth. The service check is a courtesy; the constraint is the guarantee.

An analogy: the application check is a bouncer glancing at the door; the exclusion constraint is a
turnstile that physically only lets one person through at a time.

## Consequences

- The guarantee holds under concurrent inserts, because the database serialises the check. An
  integration test in milestone 3 proves this with two racing transactions.
- A rejected insert raises an `IntegrityError`; the API layer will translate it into a `409`.
- The rule is defined once, in SQL, and cannot be bypassed by a new code path that forgets it.
- We take a dependency on PostgreSQL (not portable to SQLite), which is why the integration tests
  use a real Postgres via testcontainers rather than an in-memory substitute.
