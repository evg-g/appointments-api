# tests/property

Property-based tests: instead of hand-picked examples, they assert the **laws** the code must obey
for every input a generator can produce.

Two kinds live here:

- **`test_slot_invariants.py`** — [`hypothesis`](https://hypothesis.readthedocs.io/) over the pure
  slot computer (`services/availability.py`). No I/O; runs at unit speed. It asserts invariants such
  as "every slot has the requested duration", "no slot starts in the past", "a blackout can only
  remove slots", and that the two independent "inside working hours" implementations agree — across
  DST-active timezones.

- **`test_openapi_schemathesis.py`** — [Schemathesis](https://schemathesis.readthedocs.io/) reads
  the live `/openapi.json` and fuzzes **every** operation, asserting the headline REST property: no
  input the schema allows may cause a 500, and documented 2xx responses conform to their schema. It
  runs against real Postgres + Redis (testcontainers), authenticated as a platform admin so
  generation reaches the real handlers.

## Why the Schemathesis app has its own lifespan

Schemathesis drives each request through `starlette`'s `TestClient`, which opens and closes the
app's lifespan **per call**. The production lifespan disposes the engine and Redis client on
shutdown, so a second call would use disposed resources bound to a dead event loop. The fixture in
`conftest.py` therefore gives the fuzzed app a lifespan that *creates and disposes* its resources
each cycle — the only reliable way to fuzz an async app through the synchronous `TestClient`. The
admin user is seeded through a synchronous SQLAlchemy session so it is independent of any event loop.

Needs Docker (testcontainers). The pure `hypothesis` file does not.
