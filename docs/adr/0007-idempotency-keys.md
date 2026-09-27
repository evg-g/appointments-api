# 7. Idempotency-Key on create

- Status: accepted
- Date: 2026-09-27

## Context

A client that POSTs to create an appointment may not learn whether the request arrived: the
response can be lost to a dropped connection or a timeout even though the row was written. If the
client retries, it books a duplicate. Booking is not naturally idempotent, so the client needs a way
to say "this retry is the same request as before."

## Decision

Accept an optional `Idempotency-Key` header on `POST /api/v1/appointments`. Keyed by
`(scope, key)` in Redis, the flow is:

1. **begin** — claim the key atomically with `SET NX` and a *pending* marker that also stores a
   fingerprint (a SHA-256 of the caller's id + the exact request bytes).
2. On a repeat:
   - same key, work still pending → `409` (a concurrent duplicate is in flight);
   - same key, work finished → replay the stored response verbatim, with
     `Idempotency-Replayed: true`;
   - same key, **different** body → `422` (the key was reused for a different request — a client
     bug we refuse rather than silently mishandle).
3. **complete** — on success, overwrite the marker with the finished response (status, body, ETag),
   kept for `IDEMPOTENCY_TTL_SECONDS` (default 24h).
4. **release** — on failure, drop the pending marker so a corrected retry can start fresh; a failed
   attempt must not poison the key.

Storage sits behind an `IdempotencyKeyStore` Protocol so unit tests run the logic over `fakeredis`
and integration tests over the real Redis container.

## Consequences

- A network-blip retry with the same key returns the original resource, never a duplicate booking.
- Reusing a key for a different body is caught, not silently wrong.
- The stored record is written after the INSERT has flushed (where the double-booking constraint
  fires), so a replay never points at a row that failed to persist.
- Not a distributed transaction: if the process dies between `complete` and the DB commit, the very
  next commit still happens in the same request scope, so the window is negligible. A fully
  crash-proof version would use a transactional outbox — out of scope here and noted in KNOWN_GAPS.
