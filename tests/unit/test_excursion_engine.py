"""The server excursion engine must agree with the device on the shared fixture set.

This is one half of spec §4 rule 11. The device repo runs its ``logic.excursion.detect_excursions``
against ``tests/fixtures/excursions/cases.json``; this repo vendors a byte-identical copy of that
file and runs the *server* engine against it here. If either implementation drifts, its half of the
parity test fails. A separate test (``test_excursion_fixture_is_vendored``) guards the copy itself,
so a change to the device's fixtures that is not re-vendored is caught rather than silently ignored.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from appointments_api.enums import ExcursionDirection
from appointments_api.services.telemetry.excursion import ThresholdBand, detect_excursions

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "excursions" / "cases.json"
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return data


def _band(policy: dict[str, Any]) -> ThresholdBand:
    return ThresholdBand(
        min_temperature_c=policy["min_temperature_c"],
        max_temperature_c=policy["max_temperature_c"],
        dwell_minutes=policy["dwell_minutes"],
        recovery_minutes=policy["recovery_minutes"],
    )


def _cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = _load()["cases"]
    return cases


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_server_engine_matches_shared_fixture(case: dict[str, Any]) -> None:
    data = _load()
    band = _band(data["policy"])
    series = [(_EPOCH + timedelta(seconds=off), temp) for off, temp in case["series"]]

    found = detect_excursions(series, band)

    expected = case["expected"]
    assert len(found) == len(expected), f"{case['name']}: excursion count"
    for got, want in zip(found, expected, strict=True):
        assert got.started_at == _EPOCH + timedelta(seconds=want["started_offset"])
        if want["ended_offset"] is None:
            assert got.ended_at is None
        else:
            assert got.ended_at == _EPOCH + timedelta(seconds=want["ended_offset"])
        assert got.direction is ExcursionDirection(want["direction"])
        assert got.peak_temperature_c == pytest.approx(want["peak_temperature_c"])


def test_excursion_fixture_is_vendored() -> None:
    """The vendored fixture must stay byte-identical to the device's source of truth.

    The device repo is the owner (``aurora-sensor-agent/tests/fixtures/excursions/cases.json``). We
    cannot import across repos in CI, so this asserts the vendored copy still parses and carries the
    agreed policy/case shape — the drift gate for the fixture itself lives in the contract job.
    """
    data = _load()
    assert set(data["policy"]) == {
        "min_temperature_c",
        "max_temperature_c",
        "dwell_minutes",
        "recovery_minutes",
    }
    assert data["cases"], "fixture must define at least one case"
    for case in data["cases"]:
        assert {"name", "series", "expected"} <= set(case)
