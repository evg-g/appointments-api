# 11. Property-based and security tests, with coverage and mutation gates

- Status: accepted
- Date: 2026-09-27

## Context

Milestones 2–4 built the API and its unit + integration tests. Spec §5 asks for two more test tiers
(property-based and security) and two enforced quality gates (coverage and mutation), so a regression
in behaviour or in test strength fails CI rather than slipping through.

## Decision

- **Property tier (`tests/property/`).**
  - `hypothesis` over the pure slot computer asserts invariants for every generated input across
    DST-active timezones: slot duration, ordering, "no slot in the past", "a blackout only removes
    slots", and agreement between the two independent "inside working hours" implementations.
  - **Schemathesis** reads the live OpenAPI and fuzzes every operation for the headline REST property:
    no allowed input may cause a 500, and documented 2xx bodies conform to their schema. It fuzzes as a
    platform admin so generation reaches the real handlers.
- **Security tier (`tests/security/`).** A data-driven authorization matrix (every role × endpoint),
  JWT tampering (forged signature, `alg: none`, wrong secret, expired, refresh-as-access, and an
  inflated `role` claim that still cannot escalate because RBAC reads the role from the database),
  injection-shaped inputs (payloads stored literally or cleanly rejected, tables intact), and mass
  assignment (smuggled `id`/`status`/`version`/`is_active` ignored; no patient-id spoofing).
- **Coverage gate.** `scripts/check_coverage.py` enforces line ≥ 90% and branch ≥ 85% on `services/`
  and `api/` specifically — a scope `pytest --cov-fail-under` cannot express — computed from
  `coverage json` over all four tiers combined. Coverage runs with `concurrency = ["greenlet"]` so
  SQLAlchemy's async ORM calls are attributed correctly.
- **Mutation gate.** `mutmut` mutates the `services/` package; the fast unit suite is the runner.
  `scripts/check_mutation.py` enforces a documented baseline kill rate (see docs/TESTING.md). Because a
  full run is heavy, it runs nightly and on demand in CI, while coverage runs on every PR.

## Consequences

- CI proves the surface is crash-safe and schema-conformant, that authorization holds for every role
  on every endpoint, and that the tests are strong enough to notice a regression — not merely that
  lines ran.
- The `mutants/` directory `mutmut` generates is git-ignored and excluded from ruff and mypy.
- Some defensive branches are unreachable from the HTTP surface (e.g. a clinic that must exist being
  `None`); they are covered by unit tests where possible, and the residual few are accepted within the
  ≥ 90% / ≥ 85% budget rather than contorting tests to reach them.
- The mutation baseline records ~126 "no tests" mutants on the webhook delivery I/O paths, which the
  unit runner does not exercise; they are covered by the integration tier and excluded from the kill
  rate, which is measured over mutants that actually have covering tests.
