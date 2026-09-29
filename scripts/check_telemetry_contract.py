#!/usr/bin/env python
"""Telemetry contract drift gate — the OpenAPI gate in reverse (spec §8 rule 4).

The device repo (``aurora-sensor-agent``) owns the telemetry contract; this API vendors a copy into
``src/appointments_api/contracts/telemetry/``. Two checks:

1. **In-repo guard (always):** each vendored file's SHA-256 must match the ``CHECKSUMS.sha256``
   recorded when it was vendored. This catches a hand-edit of the vendored copy that did not come
   from a re-vendor.
2. **Cross-repo drift (when the device repo is present):** the vendored files must be byte-identical
   to the device's ``contracts/`` source. If the sibling repo is not checked out (a fork's CI), the
   check skips cleanly and stays green — the same "skip without secrets/inputs" pattern as deploys.

Point at the device repo with ``AURORA_DEVICE_REPO`` (defaults to ``../aurora-sensor-agent``).

Usage:
    uv run python scripts/check_telemetry_contract.py
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

VENDORED = Path("src/appointments_api/contracts/telemetry")
FILES = ("telemetry.schema.json", "telemetry.asyncapi.yaml")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_checksums(path: Path) -> dict[str, str]:
    recorded: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, name = line.split()
        recorded[name] = digest
    return recorded


def main() -> int:
    recorded = _read_checksums(VENDORED / "CHECKSUMS.sha256")
    for name in FILES:
        actual = _sha256(VENDORED / name)
        if actual != recorded.get(name):
            print(
                f"vendored {name} does not match CHECKSUMS.sha256 "
                f"(edited by hand?). Re-vendor from the device repo.",
                file=sys.stderr,
            )
            return 1
    print("in-repo guard: vendored telemetry contract matches its recorded checksums.")

    device_repo = Path(os.environ.get("AURORA_DEVICE_REPO", "../aurora-sensor-agent"))
    source = device_repo / "contracts"
    if not source.is_dir():
        print(f"device repo not present at {device_repo} — skipping cross-repo drift check.")
        return 0

    drifted = [
        name for name in FILES if (source / name).read_bytes() != (VENDORED / name).read_bytes()
    ]
    if drifted:
        print(
            "telemetry contract drift vs the device repo: "
            + ", ".join(drifted)
            + ".\nRe-vendor with the device repo's published contract.",
            file=sys.stderr,
        )
        return 1
    print("cross-repo check: vendored telemetry contract matches the device source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
