# Known gaps

Honest list of what is deliberately incomplete, and why. Updated as milestones land.

## CLINIC_ADMIN is not clinic-scoped yet

The data model has no link between a `CLINIC_ADMIN` user and the clinic they administer (only
`Clinician` carries a `clinic_id`). So today `CLINIC_ADMIN` is treated like `PLATFORM_ADMIN` for
appointment access and management. `PATIENT` (own only) and `CLINICIAN` (their clinic, via their
`Clinician` row) *are* correctly scoped.

**To close:** add a clinic membership for admin users (e.g. a nullable `clinic_id` on `User`, or a
membership table), then scope admin reads/writes to that clinic. Deferred to keep milestone 3
focused on the surface; tracked here so it is not forgotten.

## Coverage / mutation gates not yet enforced

Tests exist and pass, but the coverage and mutation-score gates from spec §5 are wired in
milestone 5, not here.

## OpenAPI contract not yet published

`contracts/openapi.json` and the `oasdiff` drift gate are milestone 7.
