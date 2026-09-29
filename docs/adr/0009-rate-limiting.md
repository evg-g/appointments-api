# 9. Per-principal rate limiting (fixed window)

- Status: accepted
- Date: 2026-09-27

## Context

The API needs to protect itself from a single caller flooding it (accidental retry loops, scraping,
brute force). Spec §5 asks for per-principal limits, `RateLimit-*` headers, and `429` with
`Retry-After`.

## Decision

- **Algorithm: fixed window.** Count a principal's requests in a Redis key with `INCR`; the first
  request in a window sets the key's TTL (`EXPIRE ... NX`), so the window runs from the first hit and
  resets automatically. It is the simplest correct algorithm to read and to teach. Its known
  weakness — a burst of up to ~2× the limit straddling a window boundary — is acceptable here.
- **Principal.** The access-token subject when a valid bearer token is present, else the client IP.
  The token is decoded with the same `get_settings()` the auth layer uses, so the limiter's idea of
  "who" always matches `get_current_user`.
- **Where.** HTTP middleware, so it applies uniformly and sets `RateLimit-Limit`,
  `RateLimit-Remaining`, and `RateLimit-Reset` on every response; a block returns `429` problem+json
  with `Retry-After`. Health probes and the docs are exempt.
- **Fail-open.** If the Redis counter errors, the request is allowed rather than taking the whole API
  down because the limiter's backend hiccuped.
- Registered inside the request-id middleware so even a `429` carries `X-Request-ID`.

The counter sits behind a `RateLimitStore` Protocol: unit tests use `fakeredis`, an integration test
uses a low limit against the real Redis container.

## Consequences

- Abusive callers are throttled per principal; well-behaved clients can read the headers and back off
  before being blocked.
- Limits are config (`RATE_LIMIT_*`), so environments tune them without code changes; `RATE_LIMIT_ENABLED=false` turns it off.
- A sliding-window or token-bucket limiter (smoother, no boundary burst) can replace the store later
  without touching the middleware.
