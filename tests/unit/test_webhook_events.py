"""Unit tests for webhook event mapping and JSON round-tripping. Pure, no I/O."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from appointments_api.enums import AppointmentStatus
from appointments_api.services.webhooks.events import (
    WebhookEvent,
    WebhookEventType,
    event_from_json,
    event_to_json,
    event_type_for_status,
)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (AppointmentStatus.CONFIRMED, WebhookEventType.APPOINTMENT_CONFIRMED),
        (AppointmentStatus.COMPLETED, WebhookEventType.APPOINTMENT_COMPLETED),
        (AppointmentStatus.CANCELLED, WebhookEventType.APPOINTMENT_CANCELLED),
        (AppointmentStatus.NO_SHOW, WebhookEventType.APPOINTMENT_NO_SHOW),
    ],
)
def test_event_type_for_status(status: AppointmentStatus, expected: WebhookEventType) -> None:
    assert event_type_for_status(status) is expected


def test_requested_status_maps_to_no_event() -> None:
    # Creation emits APPOINTMENT_CREATED explicitly; no transition targets REQUESTED.
    assert event_type_for_status(AppointmentStatus.REQUESTED) is None


def test_event_round_trips_through_json() -> None:
    event = WebhookEvent(
        id="evt_123",
        type=WebhookEventType.APPOINTMENT_CREATED.value,
        occurred_at=datetime(2027, 6, 1, 10, 0, tzinfo=UTC),
        data={"appointment": {"id": "abc", "status": "REQUESTED"}},
    )
    restored = event_from_json(event_to_json(event))
    assert restored == event


def test_json_is_stable_for_signing() -> None:
    # Same event → same bytes, so the signature we compute matches the body we send.
    event = WebhookEvent(
        id="evt_123",
        type="appointment.created",
        occurred_at=datetime(2027, 6, 1, 10, 0, tzinfo=UTC),
        data={"b": 2, "a": 1},
    )
    assert event_to_json(event) == event_to_json(event)
