# Branch protection

Why this exists: the pipeline only protects `main` if `main` is configured to require it. This is the
ruleset to apply, and the reasoning behind each rule.

## Ruleset for `main`

Apply as a branch protection rule (or a repository ruleset) targeting `main`:

- **Require a pull request before merging.** No direct pushes to `main`. At least **1 approving
  review**; dismiss stale approvals when new commits are pushed.
- **Require status checks to pass**, and require branches to be **up to date** before merging. Required
  checks:
  - `lint + format`
  - `PR title (conventional commits)`
  - `workflow lint (actionlint + yamllint)`
  - `mypy --strict`
  - `unit tests (3.12)` and `unit tests (3.13)`
  - `integration tests (testcontainers)`
  - `coverage gate (services/ + api/)`
  - `docker build`
  - `security (audit, scan, secrets, SBOM)`
  - `CodeQL (static analysis)`
- **Require conversation resolution** before merging.
- **Require signed commits** (optional but recommended for a portfolio repo).
- **Do not allow force pushes**; **do not allow deletions**.
- **Include administrators** — the rules apply to everyone, including the repo owner.

## The docs-only / required-checks gotcha

`ci.yml` uses `paths-ignore` so docs-only changes skip the code gates. But a **required** check that
never runs stays **pending**, which blocks the merge — the opposite of what we want.

Two ways to handle it, pick one:

1. **Simplest for a solo portfolio repo:** do not mark the path-filtered jobs as *strictly* required;
   rely on the PR review plus the checks that do run. Documented here so the choice is explicit.
2. **Robust for a team:** replace `paths-ignore` with a required "gatekeeper" job that always runs and
   reports success for docs-only changes, so the required check is always satisfied. (GitHub's
   recommended pattern for path-filtered required checks.)

This repo ships option 1 by default; option 2 is the upgrade when more than one person contributes.

## Environments

Two GitHub Environments back the deploy gates in `cd.yml`:

- **`staging`** — no reviewers; deploys automatically after a release is built.
- **`production`** — **required reviewers** set to the repo owner (or a small group). This is what
  makes `deploy-production` pause for manual approval. Optionally add a wait timer.

Set the deploy variables on the environments or the repo — see `docs/FIRST_PUSH.md`.
