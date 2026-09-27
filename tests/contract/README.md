# Contract tests

**What this tier catches:** drift between the OpenAPI schema the app actually serves and the copy
committed at `contracts/openapi.json` — the file the web app generates its client from.

**Why it exists:** the contract is the boundary between the API and its consumers. If a field is
renamed, a status code changes, or an endpoint is added and the committed schema is not regenerated,
the generated client goes stale without anyone noticing. This tier turns that into a failed build.

**How it works:** `test_openapi_drift.py` builds the app, serializes its OpenAPI document with the
exact canonical formatting used by `scripts/export_openapi.py`, and asserts it is byte-identical to
`contracts/openapi.json`. No database or network — it is a fast, I/O-free check.

**When it fails:** you changed the API surface. Run `make contract` to regenerate the committed file,
review the diff (it should describe exactly your change), and commit it.

**Related gate (CI only):** the `contract` job also runs `oasdiff` to compare the new schema against
the previous committed version and fails the PR on a *breaking* change unless the PR is labeled
`breaking-change`. See `docs/CONTRACT_WORKFLOW.md`.
