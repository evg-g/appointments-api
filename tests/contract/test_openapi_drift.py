"""Contract drift gate: the OpenAPI the app serves must match the committed contracts/openapi.json.

Why this matters: the web app generates its API client from contracts/openapi.json. If the API
surface changes but the committed file is not regenerated, the generated client silently goes stale.
This test fails the build on that drift, forcing `make contract` to be part of any API change.

It reuses the exact serialization from scripts/export_openapi.py, so "matches" means byte-identical
to the canonical form — no formatting-only diffs, and no second implementation to drift.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "contracts" / "openapi.json"
EXPORT_SCRIPT = REPO_ROOT / "scripts" / "export_openapi.py"


def _load_export_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_openapi", EXPORT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_openapi_matches_served_schema() -> None:
    export = _load_export_module()
    served = export.current_spec()

    assert CONTRACT_PATH.exists(), (
        "contracts/openapi.json is missing — generate it with `make contract`."
    )
    committed = CONTRACT_PATH.read_text(encoding="utf-8")

    assert export.matches_committed(served, committed), (
        "contracts/openapi.json is out of date with the served OpenAPI schema. "
        "Regenerate it with `make contract` and commit the result."
    )


def test_a_version_only_difference_is_not_drift() -> None:
    # release-please bumps the package version in the release PR but cannot regenerate the
    # contract, so a stale info.version alone must not fail the gate (it did, for v1.0.0).
    export = _load_export_module()
    served = export.current_spec()
    stale = copy.deepcopy(served)
    stale["info"]["version"] = "0.0.1"
    assert export.matches_committed(served, export.serialize(stale))


def test_a_real_api_change_is_still_drift() -> None:
    export = _load_export_module()
    served = export.current_spec()
    changed = copy.deepcopy(served)
    changed["paths"]["/api/v1/removed-endpoint"] = {}
    assert not export.matches_committed(served, export.serialize(changed))
    reformatted = json.dumps(served, indent=4, sort_keys=True, ensure_ascii=False) + "\n"
    assert not export.matches_committed(served, reformatted)
