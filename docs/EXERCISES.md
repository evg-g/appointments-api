# Exercises — break it on purpose

The fastest way to trust a safety net is to cut a hole in it and watch the alarm go off. Each
exercise below asks you to **break one thing**, predict **which gate should fail and why**, then run
that gate and confirm you were right. Restore, and the gate goes green again.

If you can predict the failing gate before you run it, you understand what that gate is for — and the
project is genuinely yours.

How to use this file:

1. Read the **Break** and stop. Write down your **Prediction**: which command fails, and the error.
2. Make the change.
3. Run the command under **Catch**.
4. Compare the real failure to your prediction. Then **Restore** (`git checkout -- <file>`).

All commands run in `# WSL (Ubuntu-24.04)` from the `appointments-api/` directory. Gates that need
Docker (testcontainers) are marked. See `docs/TESTING.md` for what each tier is for and
`docs/CI_CD.md` for how these map to CI jobs.

Companion exercises live in the other two repos: `appointments-web/docs/EXERCISES.md` and
`aurora-sensor-agent/docs/EXERCISES.md`.

---

## 1. Remove the double-booking exclusion constraint

**Break.** In the Alembic migration that creates the `EXCLUDE` constraint
(`migrations/versions/0001_*.py`), comment out the `ck_appointment_no_double_booking` exclusion
constraint (and its `op.execute` for `btree_gist`, if needed). Recreate the schema.

**Predict.** Which layer catches a double-booking now? The service still checks — so the *sequential*
booking test may still pass. What about two inserts racing at the same instant?

**Catch (needs Docker).**
```bash
make test-integration
```

**Why.** The service-layer check has a time-of-check/time-of-use race: two concurrent requests both
read "no overlap", then both insert. Only the database `EXCLUDE` constraint is atomic, so the
concurrent double-booking test is the one that fails without it. This is the whole reason the
constraint lives in the database, not the application — see [ADR 0002](adr/0002-prevent-double-booking-with-a-postgres-exclusion-constraint.md).

**Restore.** `git checkout -- migrations/`

## 2. Skip the `If-Match` / ETag check on a transition

**Break.** In the appointment transition/cancel path (`api/conditional.py` or the router that calls
it), stop requiring `If-Match` — accept the change without comparing the caller's `ETag` to the row's
current `version`.

**Predict.** What stops two admins from clobbering each other's edit now?

**Catch (needs Docker).**
```bash
make test-integration
```

**Why.** The ETag flow test sends a stale `If-Match` and expects `412 Precondition Failed`. Without
the check, the stale write succeeds and the test fails. This is optimistic concurrency: the `version`
column plus the `If-Match` header is how the API refuses a lost update — see
[ADR 0008](adr/0008-etag-if-match-optimistic-concurrency.md).

**Restore.** `git checkout -- src/`

## 3. Rename a field in an API response

**Break.** Rename a response field on an appointment, e.g. `starts_at` → `start_time`, in the Pydantic
output schema (`api/schemas.py`).

**Predict.** Two gates should react — one in this repo, one across the contract. Which?

**Catch.**
```bash
make test-contract      # no Docker: served schema vs committed contracts/openapi.json
make contract-diff      # oasdiff: is this a breaking change vs origin/main?
```

**Why.** The served OpenAPI no longer matches the committed `contracts/openapi.json`, so the drift
test fails. `oasdiff` classifies a removed/renamed response field as **breaking**, which fails the CI
contract gate unless the PR is labelled `breaking-change` and the version is bumped. Downstream, the
web repo's generated client would drift too (its own gate). See
[docs/CONTRACT_WORKFLOW.md](CONTRACT_WORKFLOW.md).

**Restore.** `git checkout -- src/ contracts/`

## 4. Drop the telemetry duplicate-sequence guard

**Break.** Remove the idempotency guard on telemetry: either drop the `(device_id, sequence)` unique
constraint in the migration, or change the ingestion insert from `ON CONFLICT DO NOTHING` to a plain
insert (`services/telemetry/ingestion.py`).

**Predict.** A device retries a batch after a network blip and sends the same readings twice. What
happens to the stored series, and which test notices?

**Catch (needs Docker).**
```bash
make test-integration
```

**Why.** Retries and out-of-order delivery are normal for a device that buffers offline. The
`(device_id, sequence)` key is what makes ingestion idempotent; without it a retried batch inserts
duplicate rows, the integration test that replays a batch sees the count double, and excursion
re-derivation can double-count. See [ADR 0014](adr/0014-telemetry-ingestion-and-the-telemetry-contract.md)
and [docs/TELEMETRY.md](TELEMETRY.md).

**Restore.** `git checkout -- src/ migrations/`

## 5. Reject a reading from the future — or don't

**Break.** In the telemetry ingestion clock-skew logic, stop rejecting readings whose `measured_at`
is far in the future (treat every reading as trustworthy).

**Predict.** A device with a wildly wrong clock backfills readings dated next week. Should the API
store them as-is?

**Catch (needs Docker).**
```bash
make test-integration
```

**Why.** A reading from the future is almost always a broken device clock, not real data; storing it
pollutes the series and can hide a real excursion. The rule (spec §4 rule 10) is: flag a reading
past the allowed skew, reject one from the future. The skew test asserts the future reading is
rejected; remove the check and it fails.

**Restore.** `git checkout -- src/`

## 6. Widen a request type

**Break.** Relax a request schema in `api/schemas.py` — e.g. let a `Service.duration_minutes` be zero
or negative, or make a required booking field optional with a default.

**Predict.** Which tier is designed to find "the API accepts input it should reject"?

**Catch (needs Docker).**
```bash
make test-security       # mass-assignment / bad-input tier
make test-property       # Schemathesis fuzzes every operation against the schema
```

**Why.** The security tier sends malformed and hostile inputs and expects a clean `4xx`; the
property tier (Schemathesis) generates inputs from the schema and asserts no `500` and
schema-conformant responses. A widened type either lets bad data through (security test fails) or
makes the code blow up on an input the schema now permits (property test surfaces a `500`). See
[ADR 0011](adr/0011-property-security-testing-and-coverage-mutation-gates.md).

**Restore.** `git checkout -- src/`

## 7. Break DST-aware availability

**Break.** In the slot-computation service (`services/` availability/slots), compute working-hours
windows using naive local time or a fixed UTC offset instead of the clinic's IANA timezone.

**Predict.** On the Sunday a clinic's timezone springs forward, a working day is 23 hours long. Which
test knows that?

**Catch (no Docker).**
```bash
make test        # unit tier, including the explicit DST-transition case
```

**Why.** Slots are computed in the **clinic's** timezone, then converted to UTC, so a booking lands in
the right wall-clock hour across a DST change. A fixed offset gets the spring-forward / fall-back day
wrong by an hour, and the DST unit test (with an injected fixed clock) catches it. See
[ADR 0003](adr/0003-deterministic-time-injected-clock-and-clinic-timezone-slots.md).

**Restore.** `git checkout -- src/`

## 8. Make the idempotency key stop replaying

**Break.** In the create-appointment idempotency handling (`services/idempotency.py`), ignore the
`Idempotency-Key` header — always create a new appointment.

**Predict.** A client's network drops after the server committed but before the response arrived, so
it retries the same booking. How many appointments exist now, and which test fails?

**Catch (needs Docker).**
```bash
make test-integration
```

**Why.** Replaying the same key must return the **original** resource, not create a duplicate. The
idempotency test sends the same key twice and expects one appointment and a replayed `201`; ignore
the key and it creates two. See [ADR 0007](adr/0007-idempotency-keys.md).

**Restore.** `git checkout -- src/`

## 9. Delete a test and watch the coverage gate

**Break.** Delete (or `@pytest.mark.skip`) a service-layer test module, e.g. one covering the
cancellation-window policy.

**Predict.** Line and branch coverage on `services/` + `api/` are gated (≥ 90% line / ≥ 85% branch).
Does removing a test move the *code* — or the *measurement*?

**Catch (needs Docker).**
```bash
make coverage
```

**Why.** Coverage is a measurement of the tests, not the code, so deleting a test drops the number.
If it falls under the threshold the gate fails — that is coverage doing its narrow job. The deeper
point (see [docs/TESTING.md](TESTING.md)): coverage only proves a line **ran**, not that anything
**asserted** on it, which is why the mutation gate exists too. Try the mutation angle in exercise 10.

**Restore.** `git checkout -- tests/`

## 10. Weaken an assertion and watch mutation testing

**Break.** Keep a test running but gut its assertion — e.g. change `assert response.status_code == 409`
to `assert response.status_code in range(200, 500)`, or delete the body assertions from a state-machine
test.

**Predict.** Coverage still passes (the line still ran). What catches a test that executes code but
proves nothing?

**Catch (no Docker; slow).**
```bash
make mutation
```

**Why.** Mutation testing changes the *code* (e.g. flips a comparison in `services/`) and checks that
some test *fails*. A test with a vacuous assertion executes the mutated line but never notices the
change — the mutant "survives", the kill rate drops, and the gate (≥ 78%) fails. This is the concrete
demonstration of why coverage alone is a weak signal. See
[ADR 0011](adr/0011-property-security-testing-and-coverage-mutation-gates.md).

**Restore.** `git checkout -- tests/`

## 11. Introduce a flaky test

**Break.** Add a test that depends on wall-clock time or ordering — e.g. `time.sleep(0.2)` then assert
something completed, or assert on the *order* of a list the DB does not guarantee, or seed
`random`without a fixed seed.

**Predict.** It probably passes once. What is designed to expose a test that only *usually* passes?

**Catch (needs Docker).**
```bash
make test-integration                      # try a few times, or:
uv run pytest tests/integration -p xdist -n auto   # parallel-safe check
```
and note the nightly workflow re-runs the suite (`--repeat-each` / ×3) specifically for flake
detection.

**Why.** The rules for tests (spec §5) are: no `sleep`, no inter-test order dependence, deterministic
time via an injected clock, and parallel-safe under `pytest-xdist`. A sleep-or-order-based test races
under parallelism or on a slow runner and fails intermittently. Flakiness is a real defect — a test
you cannot trust is worse than no test — which is why the nightly job hunts for it. See
[docs/CI_CD.md](CI_CD.md).

**Restore.** `git checkout -- tests/`
