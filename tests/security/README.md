# tests/security

Adversarial tests that assert the API's security properties against the real stack (Postgres + Redis
via testcontainers, no doubles — same world as `tests/integration/`).

- **`test_authz_matrix.py`** — the authorization matrix: every role against every guarded endpoint.
  Data-driven, so a new endpoint is one row and a new role fans out across all rows. Asserts that an
  unauthenticated request is 401, a forbidden role is 403, and a permitted role gets past the guard.

- **`test_jwt_tampering.py`** — the access-token guard rejects a forged signature, an `alg: none`
  token, a token signed with the wrong secret, an expired token, and a refresh-typed token replayed
  as an access token. It also proves an *inflated `role` claim does not escalate*, because
  authorization reads the role from the database, not the token.

- **`test_injection_inputs.py`** — SQL-injection shapes are treated as data: payloads are rejected by
  validation or stored and echoed back verbatim, the database is unharmed, and malformed identifiers
  produce a clean 4xx, never a 500.

- **`test_mass_assignment.py`** — smuggling server-controlled fields (`id`, `status`, `version`,
  `is_active`, `ends_at`) into a request body is ignored, and a patient cannot book on behalf of
  another patient.

Needs Docker (testcontainers).
