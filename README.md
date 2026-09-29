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

Complete. What this repo ships:

- Domain core: models, migrations, the double-booking exclusion constraint, the appointment state
  machine, and DST-aware availability (milestone 2).
- API surface under `/api/v1`: auth (JWT + rotating refresh tokens), RBAC, CRUD, availability,
  RFC 9457 problem+json, and cursor pagination (milestone 3).
- Advanced request semantics (milestone 4): `Idempotency-Key` on create, `ETag`/`If-Match`
  optimistic concurrency, per-principal rate limiting, and signed webhooks delivered by a retrying
  background worker.
- Test tiers and quality gates (milestone 5): property-based (`hypothesis` + Schemathesis) and
  security tiers, plus enforced coverage and mutation gates.
- CI/CD (milestone 6): `ci` / `cd` / `nightly` workflows — build, Trivy/gitleaks/SBOM/CodeQL,
  cosign-signed GHCR images, OIDC deploy to Azure Container Apps (skips cleanly with no secrets),
  smoke + Locust load gates. See `docs/CI_CD.md` and `docs/DEPLOYMENT.md`.
- Contract publication (milestone 7): the served OpenAPI is committed at `contracts/openapi.json`
  with a drift gate and an `oasdiff` breaking-change gate. See `docs/CONTRACT_WORKFLOW.md`.
- Telemetry ingestion (milestone 10): an MQTT worker + HTTP batch endpoint feeding one idempotent,
  order-independent ingestion service; a server-side excursion engine held to a shared fixture set
  with the device; device provisioning, time-series downsampling, excursion acknowledge, and an SSE
  stream. The device-owned telemetry contract is vendored and drift-gated. See `docs/TELEMETRY.md`.
- An admin-only audit-log read endpoint (milestone 13 addition). See `docs/adr/0015-*`.

## Docs

- [`docs/DIAGRAMS.md`](docs/DIAGRAMS.md) — the visual model (state machines, auth flow, and links)
- [`docs/TESTING.md`](docs/TESTING.md) — the test pyramid, and mock vs stub vs fake vs spy
- [`docs/API_TESTING_GUIDE.md`](docs/API_TESTING_GUIDE.md) — testing by topic, with runnable
  [`requests/*.http`](requests)
- [`docs/EXERCISES.md`](docs/EXERCISES.md) — break-it-on-purpose exercises
- [`docs/CI_CD.md`](docs/CI_CD.md), [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md),
  [`docs/CONTRACT_WORKFLOW.md`](docs/CONTRACT_WORKFLOW.md), [`docs/ERROR_CATALOG.md`](docs/ERROR_CATALOG.md),
  [`docs/TELEMETRY.md`](docs/TELEMETRY.md), and the ADRs in [`docs/adr/`](docs/adr)

## Quick start

```bash
# WSL (Ubuntu-24.04)
make setup     # create the venv and install dependencies with uv
make test      # run the test suite
make dev       # start the API on http://localhost:8000
```

Then open http://localhost:8000/health/live.

## Try the API by hand

Start the full stack and seed demo data first:

```bash
make stack-up   # Postgres + Redis + the API on http://localhost:8000
make seed       # demo users, clinic, clinician, service
```

Demo login: `admin@aurora-clinic.com` / `password123` (local demo only).

| Tool | How | Notes |
|---|---|---|
| **Swagger UI** | http://localhost:8000/docs | Browse every endpoint and send requests from the browser. The spec declares no security scheme, so there is no *Authorize* button — use it for public endpoints (`/auth/login`, `/health/*`) and for reading the schemas. |
| **ReDoc** | http://localhost:8000/redoc | Read-only reference view of the same spec. |
| **`.http` files** | [`requests/`](requests) | The recommended way. Open in VS Code with the [REST Client](https://marketplace.visualstudio.com/items?itemName=humao.rest-client) extension (or a JetBrains IDE) and click *Send Request*. Send the login request first; later requests reuse its token automatically. See [`requests/README.md`](requests/README.md). |
| **Postman** | *Import* → *Link* → `http://localhost:8000/openapi.json` (or the committed [`contracts/openapi.json`](contracts/openapi.json)) | Postman builds a collection from the spec. Call `POST /api/v1/auth/login`, then set *Authorization* → *Bearer Token* to the returned `access_token` on the collection. |

What to test, and why each flow is tested the way it is, is in
[`docs/API_TESTING_GUIDE.md`](docs/API_TESTING_GUIDE.md).

## Layout

```
src/appointments_api/   # application code (api/ -> services/ -> repositories/ -> models/)
tests/                  # unit / integration / contract / property / load / security
docs/                   # ADRs and learning docs
requests/               # hand-runnable .http files mirroring the automated tests
```

## Conventions

See `CLAUDE.md` for the conventions this repo follows (and that future AI sessions must
keep to). Contributions: see `CONTRIBUTING.md`.

## License

MIT — see `LICENSE`.
