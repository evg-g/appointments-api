# CONTEXT — appointments-api domain terms

A short glossary of the terms tickets and ADRs use. Add a term when a ticket introduces one.

- **Appointment** — a booked slot for a patient with a clinician at a clinic. Statuses:
  `REQUESTED`, `CONFIRMED`, `COMPLETED`, `CANCELLED`, `NO_SHOW`.
- **Cancel** — `POST /api/v1/appointments/{id}/cancel`. The only way to reach `CANCELLED`. Needs a
  current `If-Match` ETag (ADR 0008) and respects the clinic's cancellation cutoff.
- **Cancellation cutoff** — hours before `starts_at` after which a patient or clinician may no longer
  cancel; a clinic admin or platform admin still may.
- **Refused cancel** — a cancel request that ends in an error response (404, 403, 428, 412, 409)
  and changes nothing.
- **Audit log** — the append-only `audit_log` table and its admin-only read endpoint
  `GET /api/v1/audit-log` (ADR 0015). Rows are never edited.
- **Audit entry** — one row: `actor_id`, `action`, `entity_type`, `entity_id`, `before`, `after`,
  `created_at`.
- **Audit action** — the `AuditAction` value naming what happened, e.g. `appointment.cancelled`
  (ADR 0016).
- **Audit writer** — the `AuditWriter` Protocol the audit service records through; implemented by
  `AuditLogRepository` (ADR 0016).
