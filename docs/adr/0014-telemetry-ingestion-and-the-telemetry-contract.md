# 14. Telemetry ingestion, the server excursion engine, and the telemetry contract

- Status: accepted
- Date: 2026-09-27

## Context

Milestone 10 brings cold-chain telemetry into the API (spec §4 rules 9–11, §5, §8 rule 4). Readings
arrive from many devices over MQTT (primary) and HTTP (fallback); they retry, arrive out of order, and
backfill hours late. The server must ingest them idempotently, decide excursions the same way the
device does, expose downsampled series and a live stream, and consume a contract the device repo owns —
all in plain PostgreSQL, with no TimescaleDB.

## Decision

- **One ingestion service, two transports.** `TelemetryIngestionService` is pure orchestration over
  `typing.Protocol` seams (devices, readings, policies, excursions, publisher) and an injected clock.
  Both the HTTP batch endpoint and the MQTT worker build it from the same composition root
  (`telemetry_wiring.build_ingestion_service`), so one test suite covers both and unit tests run with
  in-memory fakes and no I/O.
- **Idempotency in the database.** `(device_id, sequence)` is unique; ingestion uses
  `INSERT ... ON CONFLICT DO NOTHING RETURNING sequence`, so duplicates — including a batch racing a
  concurrent one — are dropped atomically and reported per item.
- **Order-independence by re-derivation.** After every batch the excursion engine re-derives the whole
  series from `measured_at`, so a late backfill is still evaluated. Excursions are keyed by
  `(device_id, started_at)` and upserted, preserving acknowledgements.
- **Clock skew: flag vs reject.** A reading beyond the skew threshold from the server clock is stored
  but flagged; one beyond a small future tolerance is rejected and never stored (a device clock cannot
  invent future readings).
- **The server excursion engine is a faithful port of the device's, proven by shared fixtures.**
  `services/telemetry/excursion.py` mirrors `aurora-sensor-agent/logic/excursion.py` down to the
  dwell/recovery, flapping, and backward-clock rules. Both are run against a byte-identical copy of
  `tests/fixtures/excursions/cases.json`; each repo's half of the parity test fails if it drifts.
- **Plain PostgreSQL, downsample on read, retention as a job.** Readings sit in one table with a BRIN
  index on `measured_at`. The time-series endpoint aggregates with `date_bin` (no rollup table to keep
  in sync); `scripts/retention.py` prunes raw rows past a window to keep the table bounded.
- **SSE over a Redis Stream.** The stream id doubles as the SSE event id, so `Last-Event-ID`
  reconnection is a range query, with no per-connection server state.
- **The telemetry contract is vendored and gated in reverse.** The device repo owns the AsyncAPI
  document and the batch JSON Schema; this API vendors a byte-identical copy into the package,
  validates every inbound MQTT message against it, and `scripts/check_telemetry_contract.py` fails CI
  on drift (checksum guard always; byte compare against the device source when it is checked out). The
  payload is versioned and the API accepts version N and N−1.

## Consequences

- The excursion rule lives in two places by necessity (device and server), but the shared fixtures make
  any divergence a red test rather than a silent field disagreement.
- Re-deriving the full series per batch is O(series) and simple; for very long histories a bounded
  re-derivation window is the documented follow-up (`docs/KNOWN_GAPS.md`).
- The HTTP batch endpoint returns 200 with a per-item result array (accepted / duplicate / rejected)
  rather than a single top-level status, because a bulk endpoint cannot map one bad row to one HTTP
  code; rule 10's "422" is realized as a per-item rejection. This is documented in the error catalog.
- The SSE endpoint uses bearer auth, which `EventSource` cannot send; the browser transport (cookie or
  short-lived token) is a milestone-12 concern and is recorded as a known gap.
- The MQTT worker trusts the device id in the topic (broker ACLs bind it), not the payload body.
