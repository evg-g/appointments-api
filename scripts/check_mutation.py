#!/usr/bin/env python
"""Enforce the mutation-testing baseline for the ``services/`` package.

Coverage proves a line *ran*; it cannot prove a test would *notice* if that line were wrong.
Mutation testing does: it changes the code in small ways and checks a test fails. This gate reads
the stats ``mutmut export-cicd-stats`` writes and fails if the kill rate drops below the baseline.

"no tests" mutants (lines the unit runner does not exercise — mostly webhook I/O paths, which the
integration tier covers instead) are reported but excluded from the score, so the gate measures the
strength of the tests that *do* run against the mutated code, not merely their reach.

Usage:
    mutmut run
    mutmut export-cicd-stats
    python scripts/check_mutation.py [mutants/mutmut-cicd-stats.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Baseline kill rate over *tested* mutants (killed / (killed + survived + timeout + suspicious)).
# Documented in docs/TESTING.md. Raise this as the suite gets stronger; never lower it silently.
MIN_SCORE = 78.0


def main(argv: list[str]) -> int:
    report_path = Path(argv[1]) if len(argv) > 1 else Path("mutants/mutmut-cicd-stats.json")
    if not report_path.exists():
        print(f"mutation stats not found: {report_path}", file=sys.stderr)
        print("Run `mutmut run` then `mutmut export-cicd-stats` first.", file=sys.stderr)
        return 2

    stats = json.loads(report_path.read_text())
    killed = stats.get("killed", 0)
    survived = stats.get("survived", 0)
    timeout = stats.get("timeout", 0)
    suspicious = stats.get("suspicious", 0)
    no_tests = stats.get("no_tests", 0)
    total = stats.get("total", 0)

    tested = killed + survived + timeout + suspicious
    score = 100.0 * killed / tested if tested else 0.0
    ok = score >= MIN_SCORE

    print("Mutation gate (services/):")
    print(f"  total mutants : {total}")
    print(f"  killed        : {killed}")
    print(f"  survived      : {survived}")
    print(f"  timeout       : {timeout}")
    print(
        f"  no tests      : {no_tests}  (excluded from the score; covered by the integration tier)"
    )
    print(f"  kill rate     : {score:6.2f}%  (need >= {MIN_SCORE:.0f}%)  {'OK' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
