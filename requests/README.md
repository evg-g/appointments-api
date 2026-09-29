# `.http` request files

Hand-runnable requests that mirror the automated tests. Every flow the integration suite proves —
login and refresh rotation, booking with an idempotency key, ETag/If-Match concurrency, cursor
pagination, telemetry ingestion, webhooks, and the error contract — is here as a request you can send
by hand and watch the response.

They are the manual companion to the automated tests, and to
[`docs/API_TESTING_GUIDE.md`](../docs/API_TESTING_GUIDE.md), which explains *why* each flow is tested
the way it is.

## How to run them

These files use the [`.http` format](https://marketplace.visualstudio.com/items?itemName=humao.rest-client)
read by the **VS Code REST Client** extension and by **JetBrains** IDEs. Open a file, click *Send
Request* above any request, and the response opens beside it.

Requests are chained with named responses: `# @name login` captures the login response, and later
requests read `{{login.response.body.access_token}}` and `{{book.response.headers.ETag}}`
automatically. So in each file, **send the login request first**, then the rest reuse its token.

## Before you start

```bash
# WSL (Ubuntu-24.04), from appointments-api/
make stack-up     # Postgres + Redis + the API on http://localhost:8000
make seed         # create the demo admin + patient + a clinic/clinician/service
```

The seeded demo credentials (weak on purpose — this is a local demo, override with
`SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`):

| Role | Email | Password |
|---|---|---|
| `PLATFORM_ADMIN` | `admin@aurora-clinic.com` | `password123` |
| `PATIENT` | `patient@aurora-clinic.com` | `password123` |

`@baseUrl` defaults to `http://localhost:8000` at the top of each file — change it to point at a
deployed environment.

## The files

| File | Mirrors these tests |
|---|---|
| `auth.http` | login, `/auth/me`, refresh-token rotation, **reuse detection**, logout |
| `appointments.http` | booking, **Idempotency-Key** replay, transitions + cancel with **ETag/If-Match**, cursor pagination |
| `telemetry.http` | device provisioning, **batch ingestion** (idempotent, per-item results), time-series, excursions |
| `webhooks.http` | subscription CRUD (admin-only) |
| `errors.http` | the RFC 9457 `problem+json` contract: 401, 403, 404, 409, 412, 422 |
