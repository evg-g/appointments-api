# ADR 0017 — Commit before the response, and transaction hooks

Status: accepted (AURORA-3)

## Context

`get_session` (`db.py`) commits in the exit of a yield dependency. In FastAPI 0.141.1 the default
dependency scope runs that exit after the response is sent, so a request whose COMMIT fails still
returns its success response while the database keeps the old state. This affects every write
endpoint that uses `SessionDep`. It was found while proving AURORA-2.

Two side effects make the same mistake one step later, even once the commit moves:

- Webhook events are published to Redis before the commit (`KNOWN_GAPS.md`, ADR 0010), so a failed
  commit can still notify receivers about a change that was not saved.
- Booking with an `Idempotency-Key` stores the 201 response before the commit, so after a failed
  commit a retry with the same key replays a success for a booking that does not exist.

## Decision

- **Commit before the response.** `SessionDep` is `Depends(get_session, scope="function")`. The
  commit runs after the handler and before the response is sent, so a commit error goes through the
  normal exception handlers.
- **A catch-all error handler.** An unhandled exception returns 500 as `application/problem+json`
  (RFC 9457, ADR 0006). `IntegrityError` keeps its 409 mapping and `StaleDataError` its 412.
- **Transaction hooks.** A request-scoped `TransactionHooks` object holds two callback lists,
  `on_commit` and `on_rollback`, and is provided as a dependency. `get_session` runs `on_commit`
  after `commit()` succeeds and `on_rollback` after a rollback. Each hook runs on its own: a failing
  `on_commit` hook is logged and does not fail the request, because the change is already saved
  and the response must match the database.
- **Webhooks after commit.** `_emit` registers the publish as an `on_commit` hook instead of
  publishing during the request.
- **Booking idempotency after commit.** `IdempotencyService.complete()` is registered as an
  `on_commit` hook and `release()` as an `on_rollback` hook, so a failed commit frees the key and a
  retry with the same key is processed as a new request.
- **One way to force a failed commit in tests.** A shared integration fixture installs a test-only
  `DEFERRABLE INITIALLY DEFERRED` constraint trigger that raises at COMMIT for one chosen row, and
  always drops it.

## Consequences

- No write endpoint can return success for a change the database did not save.
- A crash, or a Redis failure, between the commit and an `on_commit` hook loses that hook's work: a
  webhook event is not published (logged), or a booking's idempotency record is not stored. In the
  second case the key's pending marker stays, so a retry with the same key gets 409 "still being
  processed" until the key's TTL (`idempotency_ttl_seconds`, 24 h) expires; the booking itself is
  saved, and a retry with a new key for the same slot is refused by the double-booking constraint.
  Exactly-once emission needs a transactional outbox; that is deferred to a later ticket (AURORA-4).
- The `KNOWN_GAPS.md` entry "Webhook emission is not transactional with the DB commit" narrows to
  that crash/Redis window, and the "commit after response" behaviour is gone.
- The behaviour depends on FastAPI's dependency `scope`. The commit-failure integration tests guard
  it against a framework change.
- No migration and no OpenAPI contract change.
