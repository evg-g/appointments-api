# ADR 0015 — Audit-log read endpoint

Status: accepted (milestone 13)

## Context

The web app (milestone 13) needs an audit-log table with server-side pagination and filters. The
project is contract-driven (ADR 0013): the web client is generated from `openapi.json` and
hand-written request/response types are forbidden. The `audit_log` table has existed since migration
0001 but was never exposed by the API, and nothing writes to it yet.

## Decision

Add a read-only endpoint `GET /api/v1/audit-log` rather than let the web app hand-write an audit type:

- **Admin only** (`CLINIC_ADMIN` / `PLATFORM_ADMIN`), reusing the `require_roles` dependency.
- **Keyset pagination** on `(created_at, id)` via the shared `keyset_page` helper and `Page[...]`
  schema — identical to every other list endpoint (ADR 0006).
- **Optional filters** (`actor_id`, `action`, `entity_type`, `entity_id`) combined with AND.
- New `AuditLogEntryOut` schema (`from_attributes=True`), `AuditLogRepository.list_entries`, and a
  `get_audit_log_repository` dependency. The endpoint is registered in the app factory like the rest.

The endpoint is additive and non-breaking (oasdiff clean); the committed `contracts/openapi.json` is
regenerated so the drift gate stays green and the web client can be generated from it.

## Consequences

- The web audit-log page is fully contract-driven and typed end-to-end.
- **The table has no write call sites yet**, so the endpoint reads an empty table in production until
  an audit-writer is added (tracked in `docs/KNOWN_GAPS.md`). This keeps milestone 13 scoped to the
  web surface while making the read path real and correct.
- Because audit rows are append-only and admin-scoped, no new migration, no new write path, and no
  change to existing endpoints were required.
