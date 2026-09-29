"""Contract tests for the vendored telemetry schema (spec §8 rules 4 and 5).

These cover the API's half of the cross-repo telemetry contract:

* the vendored copy is byte-identical to the device's source (drift guard within this repo);
* the API accepts the current schema version *and* the previous one (independent deployment);
* the JSON Schema and the OpenAPI-facing Pydantic model accept exactly the same shape (so the two
  descriptions of the payload cannot silently diverge);
* a payload shaped exactly like the device's real ``build_batches`` output validates.

The cross-repo drift gate (vendored copy vs the live device source) is ``make contract-telemetry``,
run in CI; it is not a unit test because it needs the sibling repo checked out.
"""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import Any

import pytest

from appointments_api.api.schemas import TelemetryBatchIn
from appointments_api.services.telemetry.contract import (
    CURRENT_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    ContractError,
    load_schema,
    parse_batch,
    validate_payload,
)

_CONTRACT_DIR = files("appointments_api").joinpath("contracts", "telemetry")


def _reading(v: int, *, battery: bool) -> dict[str, Any]:
    reading: dict[str, Any] = {
        "v": v,
        "measured_at": "2026-06-01T12:00:00+00:00",
        "temperature_c": 5.0,
        "humidity_pct": 45.0,
        "raw_temperature": 26000,
        "raw_humidity": 30000,
    }
    if battery:
        reading["battery_pct"] = 88.0
    return reading


def _envelope(v: int, *, battery: bool) -> dict[str, Any]:
    return {
        "v": v,
        "device_id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427",
        "idempotency_key": "abc123",
        "readings": [{"sequence": 1, "reading": _reading(v, battery=battery)}],
    }


def test_vendored_copy_matches_recorded_owner_checksum() -> None:
    """The vendored files must be byte-identical to what the device repo published (CHECKSUMS)."""
    recorded: dict[str, str] = {}
    for line in _CONTRACT_DIR.joinpath("CHECKSUMS.sha256").read_text().splitlines():
        digest, name = line.split()
        recorded[name] = digest
    assert recorded, "CHECKSUMS.sha256 is empty"
    for name, digest in recorded.items():
        blob = _CONTRACT_DIR.joinpath(name).read_bytes()
        assert hashlib.sha256(blob).hexdigest() == digest, f"vendored {name} drifted from source"


def test_schema_loads_and_declares_supported_versions() -> None:
    schema = load_schema()
    assert schema["$id"].endswith("telemetry.schema.json")
    assert set(schema["properties"]["v"]["enum"]) == set(SUPPORTED_SCHEMA_VERSIONS)


@pytest.mark.parametrize("version", sorted(SUPPORTED_SCHEMA_VERSIONS))
def test_current_and_previous_versions_accepted(version: int) -> None:
    battery = version >= 2
    envelope = _envelope(version, battery=battery)

    validate_payload(envelope)  # JSON Schema half
    device_id, readings = parse_batch(envelope)

    assert device_id == envelope["device_id"]
    assert len(readings) == 1
    assert readings[0].sequence == 1
    assert readings[0].temperature_c == 5.0
    assert readings[0].battery_pct == (88.0 if battery else None)


def test_device_id_override_wins() -> None:
    envelope = _envelope(CURRENT_SCHEMA_VERSION, battery=True)
    device_id, _ = parse_batch(envelope, device_id_override="override-id")
    assert device_id == "override-id"


def test_unsupported_version_rejected() -> None:
    envelope = _envelope(1, battery=False)
    envelope["v"] = 99  # not in the schema enum
    with pytest.raises(ContractError):
        parse_batch(envelope)


def test_missing_required_field_rejected() -> None:
    envelope = _envelope(2, battery=True)
    del envelope["readings"][0]["reading"]["temperature_c"]
    with pytest.raises(ContractError):
        validate_payload(envelope)


def test_unknown_field_rejected_by_both_schema_and_model() -> None:
    envelope = _envelope(2, battery=True)
    envelope["readings"][0]["reading"]["bogus"] = 1

    with pytest.raises(ContractError):
        validate_payload(envelope)
    with pytest.raises(ValueError):  # Pydantic extra="forbid"
        TelemetryBatchIn.model_validate(envelope)


def test_pydantic_and_jsonschema_accept_the_same_canonical_payload() -> None:
    """The OpenAPI model and the JSON Schema must agree on a valid payload, so they cannot drift."""
    envelope = _envelope(CURRENT_SCHEMA_VERSION, battery=True)

    validate_payload(envelope)  # JSON Schema accepts
    model = TelemetryBatchIn.model_validate(envelope)  # Pydantic accepts

    assert model.schema_version == CURRENT_SCHEMA_VERSION
    assert model.readings[0].sequence == 1
    assert model.readings[0].reading.temperature_c == 5.0


def test_device_build_batches_shape_validates() -> None:
    """A payload shaped like the device's ``logic.batch.build_batches`` output must validate."""
    # Mirrors aurora-sensor-agent/logic/batch._make_batch + serde.reading_to_json (v1).
    envelope = {
        "v": 1,
        "device_id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427",
        "idempotency_key": hashlib.sha256(b"1:{}").hexdigest(),
        "readings": [
            {
                "sequence": 1,
                "reading": {
                    "v": 1,
                    "measured_at": "2026-06-01T12:00:00+00:00",
                    "temperature_c": 4.4,
                    "humidity_pct": 45.2,
                    "raw_temperature": 25987,
                    "raw_humidity": 29844,
                },
            }
        ],
    }
    validate_payload(envelope)
    device_id, readings = parse_batch(envelope)
    assert device_id == envelope["device_id"]
    assert readings[0].battery_pct is None  # v1 carries no battery

    # Sanity: json round-trips (the worker json.loads the raw bytes).
    assert json.loads(json.dumps(envelope)) == envelope
