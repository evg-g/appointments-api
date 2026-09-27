# 12. Backend CI/CD pipeline

- Status: accepted
- Date: 2026-09-27

## Context

Milestones 1–5 built the API and its five test tiers with coverage and mutation gates. Spec §9/§6 asks
for the delivery half: containerize, scan, sign, publish, and deploy, with quality gates that a fresh
fork can run green with zero secrets, and everything reproducible locally.

## Decision

- **Three workflows, one composite action.** `ci.yml` (per-PR gates), `cd.yml` (release + deploy),
  `nightly.yml` (flake, mutation, load, dependency freshness). Dependency install is factored into
  `.github/actions/setup-python-uv` so no job copy-pastes the uv steps. Every job pins
  `timeout-minutes`, a minimal `permissions:` block, and caching keyed on `uv.lock`.
- **Real dependencies in CI via testcontainers.** The integration and coverage jobs start real
  Postgres and Redis from the test code itself, so the exact same code runs locally and in CI. The
  GitHub Actions `services:` alternative is documented in `docs/CI_CD.md`; testcontainers wins here
  because the concurrency tests need per-test container control.
- **Supply-chain security as gates, not reports.** `pip-audit`, `gitleaks`, Trivy filesystem + image
  scans (fail on fixable HIGH/CRITICAL), and CodeQL all fail the PR. A CycloneDX SBOM is generated from
  the image and kept as a 90-day artifact.
- **GHCR + cosign keyless + provenance.** Release images push to GHCR tagged semver + full SHA, signed
  keylessly with cosign via GitHub OIDC (no signing key to store), with a provenance attestation. All
  of this uses only the built-in `GITHUB_TOKEN` and OIDC, so it works on a repo with no configured
  secrets.
- **release-please for versioning.** Semver and the changelog are derived from Conventional Commit
  titles (enforced by a PR-title check). A release, and therefore a deploy, happens only when the
  release PR merges.
- **Azure Container Apps as the primary target, Compose as the fallback.** `infra/main.bicep` defines
  one environment (Container Apps env, Postgres, Redis, the app, and a migration Job).
  `docker-compose.prod.yml` runs the same image anywhere with a Docker host. Cloud auth is OIDC
  workload-identity federation — no client secret.
- **Deploy steps skip cleanly.** Every step that touches Azure is guarded by
  `vars.DEPLOY_ENABLED == 'true'`, so the pipeline is green end-to-end on a fork with nothing
  configured. Migrations run as a separate expand-phase step before the app rolls; a failed post-deploy
  smoke test reactivates the previous revision.

## Consequences

- The pipeline proves an image is buildable, scanned, signed, and traceable to a commit before it can
  be deployed, and a bad deploy rolls itself back.
- Because signing/publishing need GitHub's OIDC and a real registry, they cannot run under `act`; the
  local story is `make` targets for every compute gate plus `act` for the pure ones. This split is
  documented in `docs/CI_CD.md` and `docs/KNOWN_GAPS.md`.
- Expand/contract migration discipline is now a documented requirement (`docs/DEPLOYMENT.md`), because
  the rolling deploy runs old and new code against one database during the roll.
- The demo `scripts/seed.py` and the smoke/load tests assume a seeded environment; seeding is idempotent
  and refuses production without `--force`.
