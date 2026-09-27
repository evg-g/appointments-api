# 8. ETag / If-Match for optimistic concurrency

- Status: accepted
- Date: 2026-09-27

## Context

Two staff members open the same appointment and both act on it. Without a check, the second write
overwrites the first — a lost update. ADR 0004 added a `version` column and a database optimistic
lock; this ADR exposes that over HTTP so clients can participate.

## Decision

- Every appointment response (create, read, transition, cancel) carries a strong
  `ETag: "<version>"` built from the row's `version`.
- The two mutating endpoints (`/transition`, `/cancel`) require `If-Match`:
  - header absent → `428 Precondition Required` (RFC 6585);
  - header present but not the current ETag → `412 Precondition Failed`;
  - header matches → the write proceeds and bumps the version, so the response ETag moves on.
- The precondition is checked **after** authorization and the cancellation-window rule, so an
  unauthorized caller is refused without learning the resource's version.
- The check is a pure helper (`api/conditional.py`), unit-tested without HTTP. As a backstop, if two
  writers race between the read and the UPDATE, SQLAlchemy raises `StaleDataError`, which the error
  handler also maps to `412`. `flush()` happens inside the request so this surfaces as `412`, not a
  late 500 at commit.

## Consequences

- A stale write is a loud `412`, never a silent overwrite.
- Clients get a simple read-then-conditional-write loop: read the ETag, send it back in `If-Match`.
- List endpoints are unaffected (no per-row ETag on collections); conditional writes apply to single
  resources, which is where lost updates happen.
