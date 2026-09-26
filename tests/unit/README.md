# tests/unit

Pure business logic with **no I/O**: state machines, slot computation, DST math, value
objects. These run in milliseconds, need no database, network, or Docker, and use fakes or
injected protocols instead of real collaborators.

If a test here needs a real database, Redis, or an HTTP server, it belongs in
`tests/integration/`, not here.
