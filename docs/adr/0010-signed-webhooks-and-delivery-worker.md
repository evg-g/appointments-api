# 10. Signed webhooks with a retrying delivery worker

- Status: accepted
- Date: 2026-09-27

## Context

External systems want to react to appointment changes without polling. Spec §4 rule 8 asks for
webhooks signed with HMAC-SHA256 (with replay protection), delivered by a background worker with
retry and exponential backoff. Delivery is slow and can fail, so it must not happen on the request
path.

## Decision

- **Split request path from delivery.** A state change (create / confirm / complete / no-show /
  cancel) publishes **one event** to a Redis list via a tiny dispatcher and returns. No subscription
  lookup, no HTTP inline — a slow or dead receiver never slows a booking.
- **A worker does the rest.** It pops an event, finds the active subscriptions whose `event_types`
  contain it (a Postgres `@>` query), and delivers to each. It runs off by default, either in-process
  (a lifespan task) or as its own process: `python -m appointments_api.workers.webhooks`.
- **Signing.** `X-Signature: sha256=HMAC(secret, "<timestamp>.<body>")` plus `X-Webhook-Timestamp`.
  Signing the timestamp and having the receiver reject a stale one (constant-time compare) stops
  replay of a captured request. The JSON body is serialized once, deterministically, so the bytes
  signed are the bytes sent.
- **Retry with backoff, then dead-letter.** A failed delivery is scheduled in a Redis sorted set
  keyed by due-time; the delay is exponential with equal jitter (so retries don't synchronize). After
  `WEBHOOK_MAX_ATTEMPTS` it moves to a dead-letter list instead of retrying forever.
- **At-least-once, per subscription.** A receiver may see a duplicate (e.g. delivery succeeded but we
  recorded it as failed), so receivers must dedupe on `X-Webhook-Id`. This is the standard webhook
  contract.
- Every collaborator (queues, sender, clock, RNG) is injected behind a Protocol: the worker's
  fan-out / retry / dead-letter logic is unit-tested with fakes and a fixed clock (no network, no
  real time); the HTTP sender is tested with `respx`; the queues are tested against real Redis.

## Consequences

- Bookings stay fast regardless of receiver health.
- Failures are retried and, if hopeless, parked in a dead-letter list for inspection rather than lost
  or retried forever.
- **Emission is not transactional with the DB commit.** The event is published during the request,
  after the row has flushed; a crash in the tiny window between publish and commit could emit an event
  for a rolled-back change, or (reversed) commit without emitting. A transactional outbox would close
  this — deliberately out of scope, tracked in KNOWN_GAPS.
- The signing secret is stored per subscription and never returned by the API.
