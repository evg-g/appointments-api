# appointments-api

The backend for **Aurora Clinic** — appointment scheduling and medication cold-chain
monitoring. Python 3.12, FastAPI, PostgreSQL, Redis.

This repo is one of three that make up the system. See the top-level `README.md` for how
they fit together.

## Why this exists

A real scheduling + cold-chain domain, chosen so the tests have to solve genuine API
problems (concurrency, time zones, idempotency, optimistic locking, telemetry ordering)
instead of toy CRUD. The code is meant to be read and extended as a way to learn REST API
design, testing, and CI/CD.

## Status

Through milestone 4. Implemented so far:

- Domain core: models, migrations, the double-booking exclusion constraint, the appointment state
  machine, and DST-aware availability (milestone 2).
- API surface under `/api/v1`: auth (JWT + rotating refresh tokens), RBAC, CRUD, availability,
  RFC 9457 problem+json, and cursor pagination (milestone 3).
- Advanced request semantics (milestone 4): `Idempotency-Key` on create, `ETag`/`If-Match`
  optimistic concurrency, per-principal rate limiting, and signed webhooks delivered by a retrying
  background worker.

Still to come (see the top-level `PLAN.md`): property/security test tiers and coverage/mutation
gates, CI/CD, and the OpenAPI contract publication.

## Quick start

```bash
# WSL (Ubuntu-24.04)
make setup     # create the venv and install dependencies with uv
make test      # run the test suite
make dev       # start the API on http://localhost:8000
```

Then open http://localhost:8000/health/live.

## Layout

```
src/appointments_api/   # application code (api/ -> services/ -> repositories/ -> models/)
tests/                  # unit / integration / contract / property / load / security
docs/                   # ADRs and learning docs
```

## Conventions

See `CLAUDE.md` for the conventions this repo follows (and that future AI sessions must
keep to). Contributions: see `CONTRIBUTING.md`.

## License

MIT — see `LICENSE`.
