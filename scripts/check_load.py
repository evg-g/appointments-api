#!/usr/bin/env python
"""Enforce the load-test budget from Locust's CSV output.

Locust reports; this script decides pass/fail. It reads `<prefix>_stats.csv`, finds the aggregated
row, and fails if the p95 latency exceeds the budget or any request failed. Kept separate from the
Locust file so the gate is explicit and versioned, and so `make load` and the nightly job apply the
exact same rule.

Usage:
    python scripts/check_load.py --csv-prefix load --p95-ms 800 --max-failures 0
"""

from __future__ import annotations

import argparse
import csv
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Locust results against a budget.")
    parser.add_argument("--csv-prefix", default="load", help="Prefix passed to locust --csv.")
    parser.add_argument("--p95-ms", type=float, default=800.0, help="Max allowed p95 latency (ms).")
    parser.add_argument("--max-failures", type=int, default=0, help="Max allowed failed requests.")
    args = parser.parse_args()

    stats_path = f"{args.csv_prefix}_stats.csv"
    try:
        with open(stats_path, newline="") as fh:
            rows = list(csv.DictReader(fh))
    except FileNotFoundError:
        print(f"check_load: {stats_path} not found — did Locust run?", file=sys.stderr)
        return 1

    aggregated = next((r for r in rows if r.get("Name") == "Aggregated"), None)
    if aggregated is None:
        print("check_load: no 'Aggregated' row in the Locust stats.", file=sys.stderr)
        return 1

    # Column names as emitted by Locust 2.x.
    p95 = float(aggregated["95%"])
    failures = int(aggregated["Failure Count"])
    requests = int(aggregated["Request Count"])

    ok = True
    print(f"Load: {requests} requests, {failures} failures, p95 {p95:.0f}ms")
    if requests == 0:
        print("check_load: zero requests recorded — the run did nothing.", file=sys.stderr)
        ok = False
    if failures > args.max_failures:
        print(
            f"check_load: {failures} failures exceed the budget of {args.max_failures}.",
            file=sys.stderr,
        )
        ok = False
    if p95 > args.p95_ms:
        print(
            f"check_load: p95 {p95:.0f}ms exceeds the budget of {args.p95_ms:.0f}ms.",
            file=sys.stderr,
        )
        ok = False

    if ok:
        print("check_load: within budget.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
