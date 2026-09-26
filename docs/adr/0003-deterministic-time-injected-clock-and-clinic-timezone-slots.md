# 3. Deterministic time: an injected clock and clinic-timezone slot math

- Status: accepted
- Date: 2026-09-26

## Context

Scheduling is all about time, and time has two traps:

1. **The wall clock.** Code that calls `datetime.now()` directly cannot be tested without either
   waiting for real time to pass or monkey-patching the standard library. Both are bad.
2. **Timezones and DST.** A clinic's working hours are wall-clock times in its own timezone
   ("09:00–17:00 New York"), but appointments are absolute instants. On the two days a year that
   daylight saving changes, the same "09:00" is a different UTC instant than the day before.

## Decision

- **Inject a `Clock`.** Business logic takes a `Clock` protocol with a single `now()` method.
  Production uses `SystemClock`; tests pass a `FixedClock`. `freezegun` is used once as an
  independent cross-check, not as the primary mechanism.
- **Compute slots in local time, store in UTC.** `compute_slots` builds each candidate slot from
  the clinic-local calendar day and wall-clock working hours using `zoneinfo`, then converts to
  UTC. Because the conversion happens per calendar day, DST is handled by the timezone database,
  not by us. A dedicated test pins the 2026 US spring-forward: local 09:00 is 14:00Z on 2026-03-07
  and 13:00Z on 2026-03-08.
- **Availability is derived, never stored.** Slots are computed on demand from working hours minus
  existing appointments (via a repository protocol) minus blackout periods.

## Consequences

- Every time-dependent rule (cancellation window, past-slot filtering, DST) is unit-testable with
  no I/O and no flakiness.
- The DST correctness lives in one place and is proven by an explicit test, not assumed.
- The service layer depends only on protocols (`Clock`, `BusyPeriodRepository`), so the same code
  runs against an in-memory fake in unit tests and SQLAlchemy in production.
