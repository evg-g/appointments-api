# Testing strategy — appointments-api

This document explains how the test suite is layered, the test-double techniques it uses (and why),
why the integration tier uses no doubles, why coverage alone is a weak signal, and the gates that CI
enforces.

## The layers

| Layer | Location | What it proves | I/O? |
|---|---|---|---|
| Unit | `tests/unit/` | Pure business rules: slot computation, state machine, DST math, token/idempotency/rate-limit/webhook logic. Milliseconds. | none — fakes only |
| Integration | `tests/integration/` | Router + service + **real** Postgres and Redis via testcontainers: auth, RBAC, pagination, idempotency, ETag, the exclusion constraint, webhooks. | real DB + Redis |
| Property | `tests/property/` | Invariants over generated inputs: `hypothesis` for slot laws, Schemathesis fuzzing every OpenAPI operation for crash-safety and schema conformance. | slot laws: none; Schemathesis: real DB + Redis |
| Security | `tests/security/` | Authorization matrix (every role × endpoint), JWT tampering, injection-shaped inputs, mass assignment. | real DB + Redis |

Each directory has a `README.md` describing what belongs there.

## Test doubles: mock vs stub vs fake vs spy

A **double** stands in for a real collaborator. The unit tier depends on `typing.Protocol` interfaces,
never on concrete SQLAlchemy sessions or HTTP clients, so substitution is trivial. Each technique is
demonstrated at least once, with a comment naming it.

| Technique | What it is | Where in this repo |
|---|---|---|
| **Fake** | A working in-memory implementation of a protocol | `tests/fakes/repositories.py` (in-memory appointment repo), `tests/unit/test_idempotency_race.py` (`ScriptedStore`), `tests/unit/test_webhook_worker.py` (`FakeEventQueue`, `FakeDeliveryQueue`) |
| **Stub** | Returns canned values, no assertions | `FakeSubscriptionSource` returning a fixed subscription list |
| **Mock (interaction test)** | Asserts a collaborator was *called* a certain way | `tests/unit/test_webhook_dispatcher.py` — `assert_awaited_once_with` on the event queue (`autospec`d) |
| **Spy** | Wraps the real object and counts calls | `tests/unit/test_availability_no_n_plus_one.py` uses `mocker.spy` to prove `slots_for_day` queries the repository exactly once (no N+1) |
| **Fake clock** | A `Clock` returning a fixed instant | `tests/fakes/clock.py`, used by DST, cutoff-window, and worker-backoff tests |
| **HTTP mocking** | Canned HTTP responses | `tests/unit/test_webhook_sender.py` uses `respx` for success / 500 / timeout / connection error |
| **Fake Redis** | In-memory Redis | `fakeredis` in the idempotency and rate-limit unit tests |
| **DI override** | Replace a FastAPI dependency | router tests override `get_current_user` / services via `app.dependency_overrides` |
| **Async mocks** | For awaited collaborators | `unittest.mock.AsyncMock` — never a plain `MagicMock` on an `await` |

Rules for doubles: mock **only what you own** (never patch third-party internals); never mock the
object under test; prefer fakes over mocks for state, mocks only for verifying outgoing interactions;
always use `autospec=True` / `create_autospec` so a signature change breaks the test instead of
passing silently.

## Why the integration tier uses no doubles — on purpose

The unit tier proves the *logic* in isolation. The integration tier proves the *wiring, the SQL, the
constraints, and the protocol semantics* against the real infrastructure. The single most important
rule of this project — no double-booking — is enforced by a PostgreSQL `EXCLUDE` constraint over a
`tstzrange`, which **no fake can reproduce**. Substituting SQLite or an in-memory repository there
would test a fiction. So the integration tier spins up real Postgres 16 and Redis 7 with
testcontainers and uses zero doubles: if a test needs a database, it gets a real one.

## Why coverage alone is a weak signal

Line and branch coverage prove a line *ran*. They do **not** prove a test would *notice* if that line
were wrong. A test that calls a function and asserts nothing gives 100% coverage and catches nothing.

**Mutation testing** closes that gap. `mutmut` makes small changes to the code (`<` → `<=`, `+` → `-`,
`True` → `False`, deleting a call) and re-runs the tests. If the tests still pass, the mutant
*survived* — a real behaviour no test pins down. The kill rate measures the tests' power to detect
regressions, which is what we actually care about.

## Gates enforced in CI

### Coverage (`scripts/check_coverage.py`)

- **Scope:** `services/` and `api/` only (repositories, models, and workers are exercised by other
  tiers and are out of this gate's scope).
- **Thresholds:** line ≥ 90%, branch ≥ 85%.
- **Current:** line ≈ 96.6%, branch ≈ 92.4% (all four tiers combined).
- A subtlety worth recording: coverage is configured with `concurrency = ["greenlet"]` because
  SQLAlchemy's async engine runs ORM calls across greenlet switches; without it, coverage
  mis-attributes lines executed inside async request handlers and under-reports the routers.

### Mutation (`scripts/check_mutation.py`)

- **Scope:** the `services/` package; the fast, I/O-free **unit** suite is the runner.
- **Baseline (documented):** killed **421**, survived **94**, timeout **1**, no-tests **126**,
  total **642**. Kill rate over *tested* mutants (killed / (killed + survived + timeout + suspicious))
  = **≈ 81.6%**.
- **Gate:** kill rate ≥ **78%**. Raise this as the suite strengthens; never lower it silently.
- **"no tests" mutants** are lines the unit runner does not exercise — mostly the webhook delivery
  I/O paths (`sender`, parts of `worker`) which the integration tier covers instead. They are
  reported but excluded from the score, so the gate measures the strength of the tests that actually
  run against the mutated code, not merely their reach.
- Because a full mutation run is heavier than a per-PR gate should be, it runs on a schedule and on
  demand in CI (see `.github/workflows/`), matching the nightly-mutation split the spec intends,
  while coverage runs on every PR.

## Running it

```bash
make test              # unit only, fast, no Docker
make test-integration  # integration (needs Docker)
make test-property     # hypothesis + Schemathesis (needs Docker)
make test-security     # security tier (needs Docker)
make coverage          # all tiers + the coverage gate (needs Docker)
make mutation          # mutation-test services/ + the mutation gate
make ci-local          # lint + types + coverage gate — the PR gate set
```

Rules for tests: AAA structure, one behaviour per test, descriptive names, no `sleep()`, no
inter-test order dependence, parallel-safe (`pytest-xdist`), and every test must be able to fail for
the right reason.
