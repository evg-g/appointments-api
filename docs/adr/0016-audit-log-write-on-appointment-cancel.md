# ADR 0016 — Audit-log write on appointment cancel

Status: accepted (AURORA-2)

## Context

`GET /api/v1/audit-log` exists (ADR 0015), but nothing writes to the `audit_log` table, so the endpoint
returns an empty list against the real API (`docs/KNOWN_GAPS.md`). AURORA-2 adds the first write
path: one entry per successful appointment cancellation. Other actions (create, transition, devices,
excursions) come later and should reuse the same writer.

## Decision

- **A small audit service behind a Protocol.** `services/audit.py` exposes
  `record(writer, *, actor_id, action, entity_type, entity_id, before, after)` over an `AuditWriter`
  `typing.Protocol`. It has no FastAPI imports, so it is unit-tested with a fake writer and no I/O.
  `AuditLogRepository.add_entry(...)` implements `AuditWriter`.
- **A typed action vocabulary.** A new `AuditAction` StrEnum in `enums.py`, starting with
  `APPOINTMENT_CANCELLED = "appointment.cancelled"`. It is separate from `WebhookEventType`: the
  webhook set is a public contract, and audit actions will grow past it.
- **Same transaction as the cancel.** `cancel_appointment` flushes the cancel, then records the
  audit entry, then publishes the webhook. `add_entry` flushes, so an insert failure raises inside
  the handler: the request session rolls the cancel back (500) and no webhook is published.
- **Row content.** `actor_id` is the authenticated user, `entity_type` is `"appointment"`,
  `entity_id` is the appointment UUID as a string, `before` is `{"status": <old status>}` and `after`
  is `{"status": "CANCELLED"}`. The cancellation reason is not copied into the audit row.
- **Refused cancels write nothing.** Every refusal (404, 403, 428, 412, 409) is raised before the
  write. A lost-update race fails at the cancel's flush (`StaleDataError` → 412), before the write.

## Consequences

- A cancellation cannot commit without its audit row. A broken audit table blocks cancellations;
  that is the intended trade.
- No migration (the table exists since 0001) and no contract change: `AuditLogEntryOut.action`
  stays `str`, so `contracts/openapi.json` and the web client are unchanged.
- The `KNOWN_GAPS.md` entry narrows to the actions that still have no writer.
- Two concurrent cancels with the same `If-Match` always end as one 200 and one 412 with exactly one
  audit row, in any interleaving, so the race is testable with `asyncio.gather` and an outcome-set
  assertion.
