# First push — repo, protection, environments

Why this exists: the pipelines are green in this repo, but a fresh clone or a new fork needs a few
one-time settings before branch protection and deploys behave. This is that checklist. Everything
credential-dependent is optional — without it the pipeline still runs green (deploy jobs skip).

Commands use the GitHub CLI (`gh`). Replace `evg-g` with your account if you fork.

## 1. Create the repo and push

```bash
gh repo create evg-g/appointments-api --private --source=. --remote=origin --push
# make it public when you are ready to show it off:
# gh repo edit evg-g/appointments-api --visibility public --accept-visibility-change-consequences
```

## 2. Turn on branch protection

Apply the ruleset in `docs/BRANCH_PROTECTION.md`. The required checks must match the job names exactly.

```bash
gh api -X PUT repos/evg-g/appointments-api/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "lint + format",
      "PR title (conventional commits)",
      "workflow lint (actionlint + yamllint)",
      "mypy --strict",
      "unit tests (3.12)",
      "unit tests (3.13)",
      "integration tests (testcontainers)",
      "coverage gate (services/ + api/)",
      "docker build",
      "security (audit, scan, secrets, SBOM)",
      "CodeQL (static analysis)"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": { "required_approving_review_count": 1, "dismiss_stale_reviews": true },
  "restrictions": null,
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

## 3. Create the deployment environments

```bash
# staging deploys automatically; production requires a reviewer.
gh api -X PUT repos/evg-g/appointments-api/environments/staging
gh api -X PUT repos/evg-g/appointments-api/environments/production \
  -f "reviewers[][type]=User" -F "reviewers[][id]=$(gh api user -q .id)"
```

The `production` reviewer rule is what makes `deploy-production` pause for manual approval.

## 4. Configure deploy (optional — skip for a no-cloud demo)

The pipeline talks to Azure with OIDC, so there are **no client secrets** to store — only variables.
Set up the federated trust first (see `docs/DEPLOYMENT.md` §3), then:

```bash
gh variable set DEPLOY_ENABLED --body true
gh variable set AZURE_CLIENT_ID --body "<app registration client id>"
gh variable set AZURE_TENANT_ID --body "<tenant id>"
gh variable set AZURE_SUBSCRIPTION_ID --body "<subscription id>"
gh variable set AZURE_RESOURCE_GROUP --body "rg-aurora"
gh variable set STAGING_URL --body "https://staging.example.com"
gh variable set PRODUCTION_URL --body "https://api.example.com"
```

Leave `DEPLOY_ENABLED` unset (or not `true`) and every Azure step skips cleanly; the release, build,
sign, and GHCR push still run.

## 5. Nothing else is required for GHCR

Pushing images to `ghcr.io/evg-g/appointments-api` uses the built-in `GITHUB_TOKEN` with
`packages: write` — no personal access token needed. The first push publishes the package; make it
public in the repo's Packages settings if you want anonymous pulls.

## 6. Verify

- Open a PR and confirm all required checks run and are required.
- Merge the `release-please` PR it opens; confirm `cd.yml` builds, signs, and pushes an image, and
  that the Azure jobs either deploy (if configured) or skip (if not).
