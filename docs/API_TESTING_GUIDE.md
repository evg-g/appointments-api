# API testing guide

How the hard parts of this API are tested, and how to poke at them by hand. Each section pairs the
**idea** (what makes it tricky), the **automated test** that proves it, and a **hand-runnable request**
in [`requests/`](../requests) so you can watch it happen.

This complements [`docs/TESTING.md`](TESTING.md) (the test pyramid — what each *layer* is for, and
why the integration layer uses no test doubles). This file is organized by *topic* instead: pick the
behaviour you want to understand and follow it from idea to test to request.

The `.http` files are read by the VS Code REST Client extension and JetBrains IDEs — see
[`requests/README.md`](../requests/README.md) for setup. Start the stack and seed it first:

```bash
# WSL (Ubuntu-24.04), from appointments-api/
make stack-up && make seed
```

## Contents

- [Auth: tokens and refresh rotation](#auth-tokens-and-refresh-rotation)
- [Authorization (RBAC) as a business rule](#authorization-rbac-as-a-business-rule)
- [Pagination that stays stable](#pagination-that-stays-stable)
- [Idempotency on create](#idempotency-on-create)
- [Concurrency: ETag / If-Match](#concurrency-etag--if-match)
- [Time and DST](#time-and-dst)
- [Telemetry: idempotent, out-of-order ingestion](#telemetry-idempotent-out-of-order-ingestion)
- [Webhooks: signed and retried](#webhooks-signed-and-retried)
- [The error contract](#the-error-contract)
- [How a test proves it can fail](#how-a-test-proves-it-can-fail)

---

## Auth: tokens and refresh rotation

**The idea.** Access tokens are short-lived JWTs (fast to check, impossible to revoke before they
expire). Refresh tokens are opaque random strings stored in Redis, so they *can* be revoked. Each
login opens a token **family**; a refresh consumes the old token and issues a new one in the same
family. If a token that was already consumed shows up again — the signature of a stolen token being
replayed — the whole family is revoked. A leaked refresh token is therefore usable at most once
before both the attacker and the real user are logged out.

**What to test.** The happy path (login → use → refresh → use), and the two failure modes that matter:
an unknown/expired token, and **reuse of a consumed token revoking the family**. Do not just assert
"refresh returns a new token" — assert that replaying the old one kills the session.

**Automated:** `tests/integration/test_auth_flows.py`; unit rotation logic in `services/tokens.py`
tests. **By hand:** [`requests/auth.http`](../requests/auth.http) — send login, then refresh, then
replay the original refresh token and watch it 401.

## Authorization (RBAC) as a business rule

**The idea.** Roles are not just a decorator. A `PATIENT` may read/modify only their own
appointments; a `CLINICIAN` only their clinic's; a `PLATFORM_ADMIN` everything. And some rules are
authorization *and* business logic at once: cancelling past the clinic's cutoff window is rejected for
a patient but allowed for an admin.

**What to test.** Every endpoint has a `401` (no creds), `403` (wrong role), and happy-path case. The
security tier does this as a **matrix** — every role against every endpoint — so a new endpoint that
forgets a guard shows up as a hole. Crucially, the role is read from the database on each request, so
a JWT tampered to claim `PLATFORM_ADMIN` still cannot escalate.

**Automated:** `tests/security/` (authz matrix, JWT tampering). **By hand:**
[`requests/errors.http`](../requests/errors.http) — a patient creating a clinic gets `403`.

## Pagination that stays stable

**The idea.** Offset pagination (`?page=2`) breaks when rows are inserted while you page — you skip or
repeat items. This API uses **keyset (cursor) pagination**: `?limit=&cursor=`, returning
`{ data, page: { next_cursor, has_more } }`. The cursor encodes the last row's sort key, so new
inserts do not shift the window.

**What to test.** Page through a list, insert a row mid-pagination, and prove the second page neither
skips nor duplicates. That instability is exactly what offset pagination gets wrong.

**Automated:** the cursor-stability integration test. **By hand:**
[`requests/appointments.http`](../requests/appointments.http) — the last two requests follow
`page.next_cursor`.

## Idempotency on create

**The idea.** Networks drop responses. A client that sent `POST /appointments`, got no reply, and
retries must not create two appointments. The client sends an `Idempotency-Key` header; the server
claims it in Redis (`SET NX`) with a fingerprint of the request. A replay with the same key returns
the **original** resource; the same key with a *different* body is a client bug and returns `422`.

**What to test.** Same key + same body → one appointment, replayed `201`. Same key + different body →
`422`. Key claimed but request still in flight → `409`. Release-on-failure so a failed attempt does
not burn the key.

**Automated:** `tests/integration/test_appointments_idempotency.py`; unit level uses `fakeredis`.
**By hand:** [`requests/appointments.http`](../requests/appointments.http) — the three `demo-key-0001`
requests.

## Concurrency: ETag / If-Match

**The idea.** Two staff open the same appointment; both edit. Without protection the second write
silently overwrites the first (a lost update). Every appointment response carries a strong `ETag`
derived from its `version` column. A write must send `If-Match: <etag>`; if the row moved on, the
caller's ETag is stale and the API returns `412 Precondition Failed`. A write with no `If-Match` at
all returns `428 Precondition Required`.

**What to test.** Read → write with the current ETag succeeds and returns a new ETag. Write again with
the *old* ETag → `412`. Write with no `If-Match` → `428`.

**Automated:** the ETag flow integration test (`api/conditional.py`). **By hand:**
[`requests/appointments.http`](../requests/appointments.http) — confirm with the fresh ETag, then
retry with the stale one for the `412`.

## Time and DST

**The idea.** Appointments are booked in a clinic's **local** timezone but stored in UTC. On the day a
clinic's zone springs forward, a "9am–5pm" day is not 8 hours of UTC it was yesterday. Get the
timezone maths wrong and slots land an hour off, twice a year.

**What to test.** Slot computation with an **injected fixed clock** across a real DST transition, so
the test is deterministic and the spring-forward/fall-back day is explicitly checked. The clock is a
`Protocol`, never `datetime.now()`, which is what makes this testable at all.

**Automated:** `tests/unit/` slot/availability + the explicit DST case; `freezegun` as a cross-check.
**By hand:** [`requests/appointments.http`](../requests/appointments.http) — `GET /availability` for a
day, and note the slots are in the clinic's wall-clock time.

## Telemetry: idempotent, out-of-order ingestion

**The idea.** A fridge sensor buffers readings when offline and floods them when it reconnects — so
batches arrive late, out of order, or twice. Ingestion is keyed by `(device_id, sequence)`: duplicates
are dropped (`INSERT ... ON CONFLICT DO NOTHING`), and after every batch the excursion engine
re-derives the whole series from `measured_at`, so a late backfill still raises the right excursion.
A reading from the future (a broken device clock) is rejected, not trusted.

**What to test.** Retry a batch → no duplicate rows. Deliver batches out of order → same result as in
order. Backfill a gap → the excursion that spans it is detected. Future reading → rejected.

**Automated:** `tests/integration/test_telemetry_*.py`; the device and server excursion engines are
held to a shared fixture set. **By hand:**
[`requests/telemetry.http`](../requests/telemetry.http) — provision a device, send a batch, replay it
(watch the `duplicates` count), and send a future reading (watch it land in `rejected`).

## Webhooks: signed and retried

**The idea.** State changes (an appointment created or cancelled) fan out to subscribers. Deliveries
are signed with HMAC-SHA256 (`X-Signature: sha256=…`) plus a signed timestamp so a receiver can verify
authenticity and reject replays. A background worker delivers them with retry and exponential backoff,
dead-lettering after the max attempts; receivers dedupe on `X-Webhook-Id` because delivery is
at-least-once.

**What to test.** Subscription CRUD is admin-only. Delivery: success, a `500` that retries, a timeout,
a connection error — each mocked at the HTTP boundary with `respx`, and the dispatcher call asserted
exactly once with the right payload.

**Automated:** `tests/integration/test_webhooks_api.py` (CRUD) + worker unit/integration tests.
**By hand:** [`requests/webhooks.http`](../requests/webhooks.http) — create/list/delete a subscription;
a patient gets `403`.

## The error contract

**The idea.** Every error is machine-readable in one shape: **RFC 9457 `application/problem+json`** —
a stable `type` URI, `title`, `status`, `detail`, `instance`, and for validation an `errors[]` array
mapping each problem to its field. Clients (including the web app's form layer) can rely on that shape
instead of scraping prose.

**What to test.** Each status has the right `type` and body: `401`, `403`, `404`, `409`, `412`, `422`.
The full list is [`docs/ERROR_CATALOG.md`](ERROR_CATALOG.md).

**Automated:** woven through the integration + security tiers. **By hand:**
[`requests/errors.http`](../requests/errors.http).

## How a test proves it can fail

A test that cannot fail proves nothing. Two habits keep these honest:

- **Break the code on purpose** and confirm the matching test goes red. That is exactly what
  [`docs/EXERCISES.md`](EXERCISES.md) walks through — remove the exclusion constraint, skip the ETag
  check, drop the duplicate-sequence guard — and predict which gate catches it.
- **Mutation testing** (`make mutation`) does this automatically for `services/`: it changes the code
  and checks that some test fails. A test that runs a line but asserts nothing lets the mutant survive,
  which is why coverage alone is not enough. See
  [ADR 0011](adr/0011-property-security-testing-and-coverage-mutation-gates.md).
