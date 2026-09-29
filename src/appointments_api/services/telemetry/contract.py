"""The telemetry contract, as the API sees it.

The device repo (``aurora-sensor-agent``) owns the telemetry contract — an AsyncAPI 3 document plus
a JSON Schema for the batch payload. This API **vendors** a byte-identical copy into the package
(``appointments_api/contracts/telemetry/``) and validates *every* inbound message against it, so a
device that drifts from the agreed shape is rejected at the door rather than corrupting the series.
CI fails on drift between the vendored copy and the device's source — the OpenAPI gate in reverse
(spec §8 rule 4).

Payload versioning (spec §8 rule 5): every message carries a schema version. The API accepts the
current version and the previous one, and a contract test proves it, so device and server can be
deployed independently. v1 payloads simply omit ``battery_pct``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator

from appointments_api.services.telemetry.ingestion import ReadingInput

CURRENT_SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})

_SCHEMA_RESOURCE = "telemetry.schema.json"


class ContractError(Exception):
    """An inbound telemetry message did not conform to the vendored contract."""


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    """Load the vendored telemetry JSON Schema from the package data."""
    resource = files("appointments_api").joinpath("contracts", "telemetry", _SCHEMA_RESOURCE)
    schema: dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    return schema


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema())


def validate_payload(data: object) -> None:
    """Raise :class:`ContractError` if ``data`` does not conform to the vendored schema."""
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        location = "/".join(str(p) for p in first.path) or "<root>"
        raise ContractError(f"telemetry payload violates contract at {location}: {first.message}")


def _parse_measured_at(raw: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ContractError(f"measured_at is not ISO-8601: {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_batch(
    data: dict[str, Any], *, device_id_override: str | None = None
) -> tuple[str, list[ReadingInput]]:
    """Validate an envelope against the contract and turn it into ``(device_id, readings)``.

    ``device_id_override`` lets the HTTP endpoint trust the authenticated path device id over the
    body's; the MQTT worker passes the id parsed from the topic instead.
    """
    validate_payload(data)
    version = data["v"]
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ContractError(
            f"unsupported telemetry schema version {version}; "
            f"this API accepts {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    device_id = device_id_override or str(data["device_id"])
    readings = [
        ReadingInput(
            sequence=int(item["sequence"]),
            measured_at=_parse_measured_at(item["reading"]["measured_at"]),
            temperature_c=float(item["reading"]["temperature_c"]),
            humidity_pct=_optional_float(item["reading"].get("humidity_pct")),
            battery_pct=_optional_float(item["reading"].get("battery_pct")),
        )
        for item in data["readings"]
    ]
    return device_id, readings


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "ContractError",
    "load_schema",
    "parse_batch",
    "validate_payload",
]
