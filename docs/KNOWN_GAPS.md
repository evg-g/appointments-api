# Known gaps

Honest list of what is deliberately incomplete, and why. Updated as milestones land.

## CLINIC_ADMIN is not clinic-scoped yet

The data model has no link between a `CLINIC_ADMIN` user and the clinic they administer (only
`Clinician` carries a `clinic_id`). So today `CLINIC_ADMIN` is treated like `PLATFORM_ADMIN` for
appointment access and management. `PATIENT` (own only) and `CLINICIAN` (their clinic, via their
`Clinician` row) *are* correctly scoped.

**To close:** add a clinic membership for admin users (e.g. a nullable `clinic_id` on `User`, or a
membership table), then scope admin reads/writes to that clinic. Deferred to keep milestone 3
focused on the surface; tracked here so it is not forgotten.

## Coverage / mutation gates not yet enforced

Tests exist and pass, but the coverage and mutation-score gates from spec §5 are wired in
milestone 5, not here.

## OpenAPI contract not yet published

`contracts/openapi.json` and the `oasdiff` drift gate are milestone 7.

## Webhook emission is not transactional with the DB commit

A state change publishes its event to Redis during the request, after the row has flushed but before
the surrounding transaction commits. A crash in that small window could emit an event for a change
that later rolled back, or commit a change whose event was not emitted. Receivers already must dedupe
on `X-Webhook-Id` (delivery is at-least-once), but exactly-once *emission* needs a transactional
outbox: write the event to a DB table in the same transaction, and have a relay publish it. Deliberately
out of scope for milestone 4; see ADR 0010.

## Webhook worker runs on demand, and dead letters are not yet surfaced

The delivery worker is off by default (`WEBHOOK_WORKER_ENABLED=false`); run it in-process by enabling
that flag, or as its own process (`python -m appointments_api.workers.webhooks`). Dead-lettered
deliveries land in the `webhook:dead` Redis list; there is no admin endpoint or alert to inspect or
replay them yet.
