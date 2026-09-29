#!/usr/bin/env python
"""Enforce per-package coverage gates on ``services/`` and ``api/``.

``pytest --cov-fail-under`` can only enforce a single line-coverage number over everything measured.
The spec (milestone 5) requires **line >= 90% and branch >= 85%, on the ``services/`` and ``api/``
packages specifically** — repositories, models, and workers are covered by other tiers and are not
part of this gate. So we read ``coverage json`` and compute the two figures over just those files.

Usage:
    coverage run -m pytest tests/unit tests/integration tests/security tests/property
    coverage json -o coverage.json
    python scripts/check_coverage.py [coverage.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

LINE_THRESHOLD = 90.0
BRANCH_THRESHOLD = 85.0

# Files whose path contains one of these segments are in scope for the gate.
SCOPED_SEGMENTS = ("/appointments_api/services/", "/appointments_api/api/")


def _in_scope(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(segment in normalized for segment in SCOPED_SEGMENTS)


def main(argv: list[str]) -> int:
    report_path = Path(argv[1]) if len(argv) > 1 else Path("coverage.json")
    if not report_path.exists():
        print(f"coverage report not found: {report_path}", file=sys.stderr)
        return 2

    data = json.loads(report_path.read_text())
    files = {path: info for path, info in data["files"].items() if _in_scope(path)}
    if not files:
        print("no files matched the coverage scope (services/, api/)", file=sys.stderr)
        return 2

    covered_lines = sum(f["summary"]["covered_lines"] for f in files.values())
    num_statements = sum(f["summary"]["num_statements"] for f in files.values())
    covered_branches = sum(f["summary"]["covered_branches"] for f in files.values())
    num_branches = sum(f["summary"]["num_branches"] for f in files.values())

    line_pct = 100.0 * covered_lines / num_statements if num_statements else 100.0
    branch_pct = 100.0 * covered_branches / num_branches if num_branches else 100.0

    line_ok = line_pct >= LINE_THRESHOLD
    branch_ok = branch_pct >= BRANCH_THRESHOLD

    print("Coverage gate (services/ + api/):")
    print(f"  files in scope : {len(files)}")
    print(
        f"  line coverage  : {line_pct:6.2f}%  "
        f"(need >= {LINE_THRESHOLD:.0f}%)  {'OK' if line_ok else 'FAIL'}"
    )
    print(
        f"  branch coverage: {branch_pct:6.2f}%  "
        f"(need >= {BRANCH_THRESHOLD:.0f}%)  {'OK' if branch_ok else 'FAIL'}"
    )

    if line_ok and branch_ok:
        return 0

    # Show the least-covered files to make a failure actionable.
    print("\nLowest line coverage in scope:")
    worst = sorted(
        files.items(),
        key=lambda kv: kv[1]["summary"]["percent_covered"],
    )[:10]
    for path, info in worst:
        s = info["summary"]
        print(f"  {s['percent_covered']:6.2f}%  {path}  (missing lines: {s['missing_lines']})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
