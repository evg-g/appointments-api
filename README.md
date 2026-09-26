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

Milestone 1 (scaffolding). The app currently serves health checks only; domain features
land in later milestones (see the top-level `PLAN.md`).

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
