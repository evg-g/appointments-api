# tests/integration

Router + service + **real** database and Redis, wired through the ASGI app with `httpx.AsyncClient`.
Containers come from [testcontainers](https://testcontainers.com/): real Postgres 16 (so the
double-booking exclusion constraint is genuinely exercised) and real Redis 7 (so refresh-token
rotation, idempotency, and rate limiting run against the real thing).

**No test doubles here — on purpose.** The unit tier proves the logic in isolation with fakes; the
integration tier proves the wiring, the SQL, the constraints, and the protocol semantics against the
real infrastructure. Mixing a mock in would defeat the point. See `docs/TESTING.md` for the full
mock-vs-fake rationale.

What it covers: auth login/refresh/rotation/reuse, the RBAC matrix, working-hours validation,
sequential **and concurrent** double-booking (409 via the exclusion constraint), the appointment
state machine, the cancellation window, cursor-pagination stability under concurrent inserts,
idempotency replay/reuse, ETag/If-Match (428/412), rate limiting (429 + headers), and webhook
enqueue/deliver/retry/dead-letter.

Each test starts from a truncated database and a flushed Redis. Needs Docker running.
