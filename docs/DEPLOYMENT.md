# Deployment

Why this exists: it explains how `appointments-api` gets from a merged commit to a running service,
and how to run the same thing without a cloud account. Read `docs/CI_CD.md` first for the pipeline
that drives all of this.

There are two deployment targets:

1. **Azure Container Apps** — the primary target, defined as code in `infra/main.bicep`.
2. **`docker-compose.prod.yml`** — a no-cloud fallback so the whole thing is runnable on any Docker
   host, including the pipeline when `DEPLOY_ENABLED` is not set.

---

## 1. The short version

On push to `main`, `cd.yml` runs `release-please`. When a release PR merges, a semver tag is cut and
the pipeline builds the image, pushes it to GHCR (signed with cosign, with a provenance attestation),
then — **only if `vars.DEPLOY_ENABLED == 'true'`** — deploys to staging, smoke-tests, waits for
manual approval, and deploys to production.

With no Azure configured, everything up to and including "push signed image to GHCR" still runs and
stays green; the Azure steps skip cleanly.

---

## 2. Azure Container Apps

### Resources (`infra/main.bicep`)

One deployment creates, per environment: a Log Analytics workspace, a Container Apps managed
environment, a PostgreSQL flexible server + database, an Azure Cache for Redis, the API container app
(external ingress, HTTP autoscaling, liveness/readiness probes), and a **migration Job**.

Deploy it manually (or from the pipeline):

```bash
export IMAGE=ghcr.io/evg-g/appointments-api:1.2.3
export DB_ADMIN_PASSWORD=$(openssl rand -hex 24)
export JWT_SECRET=$(openssl rand -hex 32)

az deployment group create \
  --resource-group rg-aurora-staging \
  --template-file infra/main.bicep \
  --parameters infra/staging.bicepparam
```

Secrets (`DB_ADMIN_PASSWORD`, `JWT_SECRET`) are read from the environment by the `.bicepparam` files
and stored as Container Apps secrets. Nothing sensitive is committed.

### Why a separate migration Job

The app image and the schema migrate independently. Running `alembic upgrade head` inside the API
container's startup would race across replicas and couple "app is up" with "schema changed". Instead
the migration is a **Container Apps Job** (`migrate-<env>`) that the pipeline starts *before* it rolls
the new app revision. It runs once, exits, and its success gates the deploy.

---

## 3. OIDC — no long-lived cloud secrets

The pipeline authenticates to Azure with **workload-identity federation (OIDC)**. GitHub mints a
short-lived token for the workflow run; Azure trusts it for a specific repo + ref and hands back a
scoped access token. There is **no client secret stored in GitHub**.

### Trust policy (one-time setup)

1. Create an Entra ID app registration (or a user-assigned managed identity) and a service principal.
2. Add a **federated credential** on it:
   - Issuer: `https://token.actions.githubusercontent.com`
   - Subject for the production environment:
     `repo:evg-g/appointments-api:environment:production`
   - Subject for staging: `repo:evg-g/appointments-api:environment:staging`
   - Audience: `api://AzureADTokenExchange`
3. Grant the principal `Contributor` (or a tighter custom role) on the target resource group.
4. Set repo **variables** (not secrets): `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
   `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `STAGING_URL`, `PRODUCTION_URL`, and
   `DEPLOY_ENABLED=true`.

The subject pinning is what makes this safe: only a run *in this repo* targeting *that environment*
can obtain the token. A fork cannot.

---

## 4. Migrations: expand / contract

Never make a breaking schema change in one step behind a rolling deploy — during the roll, old and
new code run at the same time against the same database. Use the two-phase pattern:

| Phase | What you do | Why it is safe |
|---|---|---|
| **Expand** | Add the new column/table/index. Make it nullable or defaulted. Deploy. | Old code ignores it; new code can use it. Both work. |
| **Migrate** | Backfill data; start writing to the new shape while still reading the old. | No reader sees a missing value. |
| **Contract** | Once every replica is on new code, drop the old column/constraint in a later release. | Nothing references the old shape any more. |

Concretely: renaming a column is `add new` → `backfill` → `dual-write` → `switch reads` → `drop old`,
spread across releases — never a single `ALTER ... RENAME`. The exclusion constraint and other
invariants are added in the expand phase and are safe because they only reject genuinely bad writes.

`cd.yml` runs only the **expand** step automatically (`alembic upgrade head`). Contract migrations are
deliberate, reviewed, and shipped in a later release once the old code is gone.

---

## 5. Rollback

- **App**: `cd.yml` runs the smoke test after each deploy. On failure it reactivates the previous
  Container Apps revision, so a bad image never stays live. Container Apps keeps revisions, so this is
  a fast traffic switch, not a rebuild.
- **Database**: schema rollback is **not** automatic. Expand/contract is what makes rollback safe —
  because expand migrations are additive, rolling the app back to the previous revision still works
  against the newer schema. A destructive contract migration is only shipped after the app that needed
  the old shape is fully gone, so there is nothing to roll back to that would need it.

---

## 6. No-cloud fallback: `docker-compose.prod.yml`

Runs the published image with Postgres and Redis on any Docker host:

```bash
export IMAGE=ghcr.io/evg-g/appointments-api:1.2.3
export POSTGRES_PASSWORD=$(openssl rand -hex 24)
export JWT_SECRET=$(openssl rand -hex 32)
docker compose -f docker-compose.prod.yml up -d
```

It fails fast if `IMAGE`, `POSTGRES_PASSWORD`, or `JWT_SECRET` are unset, instead of booting with the
insecure dev key. The `migrate` service runs `alembic upgrade head` before the API starts, mirroring
the Container Apps migration Job.

---

## 7. Verifying the image signature

Anyone can verify a released image was built by this repo's pipeline:

```bash
cosign verify \
  --certificate-identity-regexp "https://github.com/evg-g/appointments-api/.github/workflows/cd.yml@.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/evg-g/appointments-api:1.2.3
```

The provenance attestation (who built it, from which commit, with which workflow) is attached to the
image in GHCR and viewable with `gh attestation verify` or `cosign verify-attestation`.
