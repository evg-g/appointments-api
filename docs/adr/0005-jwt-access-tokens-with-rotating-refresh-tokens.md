# 5. JWT access tokens with rotating refresh tokens

- Status: accepted
- Date: 2026-09-27

## Context

We need stateless, cheap authorization on every request, but also the ability to revoke a
session (logout, or a stolen token). Pure JWTs cannot be revoked before they expire; pure
server-side sessions cost a lookup on every request.

## Decision

Two token types:

- **Access token**: a short-lived (15 min) JWT carrying `sub` (user id) and `role`. It is verified
  with a signature check only — no database hit — on every request. Its short life bounds the
  damage if it leaks.
- **Refresh token**: a long-lived, opaque random string stored in Redis, used only at
  `/auth/refresh`. It is **single-use**: every refresh consumes the presented token and issues a
  new one in the same *family*. Passwords are hashed with **Argon2**.

**Reuse detection.** If a refresh token that was already consumed is presented again — the classic
signature of a stolen token being replayed — the whole family is revoked, logging out both the
attacker and the legitimate user, who must log in again. This turns token theft into a detectable,
contained event instead of a silent compromise.

## Consequences

- Normal requests are validated with no I/O; only refresh touches Redis.
- Logout and theft are both handled by revoking a family.
- Redis is now on the auth path; `/health/ready` checks it. If Redis is down, refresh and logout
  fail, but access tokens already issued keep working until they expire.
- Token rotation logic lives in a pure `TokenService` over a `RefreshTokenStore` protocol, so it is
  unit-testable; the integration tests prove rotation and reuse detection against real Redis.
