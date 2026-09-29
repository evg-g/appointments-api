#!/usr/bin/env python
"""Export the served OpenAPI schema to contracts/openapi.json.

Why this exists: the web app generates its API client from this file, and CI fails if the schema the
app serves drifts from the committed copy (see tests/contract/ and the `contract` CI job). So the
committed file is the single source of truth for the HTTP contract, and it must be regenerated
whenever the API surface changes.

The output is deterministic — keys sorted, stable indentation, trailing newline — so a real API
change produces a small, reviewable diff instead of noise.

Usage:
    uv run python scripts/export_openapi.py            # write contracts/openapi.json
    uv run python scripts/export_openapi.py --check     # exit 1 if the committed file is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from appointments_api.main import create_app

DEFAULT_OUTPUT = Path("contracts/openapi.json")


def current_spec() -> dict[str, Any]:
    """Return the OpenAPI document the running app serves."""
    # create_app() builds the engine/redis lazily (no connection opened), so this needs no database.
    app = create_app()
    spec: dict[str, Any] = app.openapi()
    return spec


def serialize(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or check the OpenAPI contract.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit non-zero if the committed file differs from the served schema.",
    )
    args = parser.parse_args()

    rendered = serialize(current_spec())

    if args.check:
        if not args.output.exists():
            print(f"{args.output} does not exist — run scripts/export_openapi.py.", file=sys.stderr)
            return 1
        committed = args.output.read_text(encoding="utf-8")
        if committed != rendered:
            print(
                f"{args.output} is out of date with the served OpenAPI schema.\n"
                "Regenerate it with:  make contract",
                file=sys.stderr,
            )
            return 1
        print(f"{args.output} matches the served schema.")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
