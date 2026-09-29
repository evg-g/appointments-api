# 13. OpenAPI contract publication and the oasdiff gate

- Status: accepted
- Date: 2026-09-27

## Context

The web app generates its API client from the backend's OpenAPI schema (spec §6, §8). For that to be
safe, the schema must be a committed, reviewable artifact, and CI must fail when the API drifts from it
or breaks it. The three repos deploy independently, so a breaking change needs a deliberate, staged
rollout, not a silent flag day.

## Decision

- **Publish the schema to `contracts/openapi.json`.** `scripts/export_openapi.py` renders
  `app.openapi()` deterministically (keys sorted, stable indentation, trailing newline) so a real API
  change produces a small diff and a formatting change produces none. `make contract` regenerates it;
  the CI `contract` job publishes it as a build artifact.
- **Drift gate (`tests/contract/`).** A fast, I/O-free test asserts the served schema is byte-identical
  to the committed file, reusing the export script's own serializer so there is no second
  implementation to drift. It runs on every PR and is part of the coverage run.
- **Breaking-change gate (`oasdiff`).** The `contract` CI job compares the PR's schema against the base
  branch's committed schema with `oasdiff breaking --fail-on ERR`. A breaking change fails the PR
  unless it is labeled `breaking-change` (the deliberate acknowledgement) and the version is bumped.
  The same binary and command are available locally via `make contract-diff`.
- **Cross-repo rollout is expand/contract.** Breaking changes ship as add → deprecate → migrate →
  remove across releases, documented in `docs/CONTRACT_WORKFLOW.md` with sequence diagrams. This is the
  same discipline as the database migrations, applied to the API surface.

## Consequences

- The committed contract is the single source of truth; the web app can regenerate its client from it
  and detect drift in the opposite direction (its own CI, milestone 12).
- `oasdiff` is pinned (v1.32.1) and downloaded in CI rather than taken from a floating action tag, so
  local and CI behaviour match exactly. It is not installed by default locally; `make contract-diff`
  needs it on PATH.
- The breaking-change label is an intentional escape hatch, not a bypass: it still requires a human to
  choose it and a version bump to go with it.
- The telemetry contract (AsyncAPI + JSON Schema, the opposite direction) is owned by the device repo
  and consumed here in a later milestone; it will use the same gate shape in reverse.
