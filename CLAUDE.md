# CLAUDE.md — appointments-api conventions

Read this before changing anything in this repo.

## What this repo is

FastAPI backend for Aurora Clinic (scheduling + cold-chain telemetry). Layered and testable.

## Architecture rules

- Layers: `api/` (routers, deps, schemas) → `services/` (business logic, **no FastAPI
  imports**) → `repositories/` (SQLAlchemy) → `models/`.
- Services depend on `typing.Protocol` interfaces, never on concrete sessions or HTTP
  clients, so unit tests can substitute fakes with no I/O.
- Dependency injection via FastAPI `Depends`. Settings via `pydantic-settings`, validated
  at startup.

## Non-negotiables

- No placeholders, no `TODO`, no stubbed functions. Everything runnable.
- Type-clean: `mypy --strict` and `ruff` must pass. No unexplained `# type: ignore`.
- Tests for every behaviour. Unit tests do **no** I/O. Integration tests use real Postgres
  and Redis via testcontainers — no SQLite substitution.
- Errors use RFC 9457 `application/problem+json`.
- Conventional Commits, small steps.

## Commands

```bash
make setup          # uv venv + install
make dev            # run the API locally
make test           # unit tests
make test-integration
make lint / make fix
make ci-local       # the full PR gate set
```

## Tooling

Python 3.12 · uv · ruff (lint+format) · mypy --strict · pytest. Dependencies pinned;
`uv.lock` committed.
