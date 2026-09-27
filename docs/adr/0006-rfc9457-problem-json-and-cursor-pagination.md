# 6. RFC 9457 problem+json errors and cursor pagination

- Status: accepted
- Date: 2026-09-27

Two API-surface decisions from milestone 3, recorded together.

## Errors: `application/problem+json`

### Context

Ad-hoc `{"detail": "..."}` errors force clients to string-match prose, which breaks the moment we
reword a message.

### Decision

Every error is an RFC 9457 problem document with a stable `type` URI (the machine key), a `title`,
`status`, `instance`, and, for validation, a machine-readable `errors[]`. A central handler set
(`api/errors.py`) maps API errors, request-validation failures, domain errors, and the database
integrity error (the double-booking constraint) to problems. The full list is in
docs/ERROR_CATALOG.md.

### Consequences

- Clients branch on `type`; we can reword titles freely.
- The double-booking exclusion constraint surfaces as a clean `409 slot-unavailable`, not a 500.

## Pagination: cursor (keyset), not offset

### Context

Offset pagination (`?page=2`) repeats or skips rows when data is inserted while a client is paging
— common on an active clinic calendar.

### Decision

Keyset pagination on `(created_at, id)` descending. The response is
`{ data, page: { next_cursor, has_more } }`; the cursor is an opaque base64 token pointing at the
last returned row. The next request asks for rows strictly before it. We fetch `limit + 1` rows to
compute `has_more` without a second COUNT.

### Consequences

- Paging is stable under concurrent inserts (proven by an integration test: a row inserted
  mid-pagination neither duplicates nor skips existing rows).
- Clients cannot jump to an arbitrary page number — an accepted trade for correctness.
