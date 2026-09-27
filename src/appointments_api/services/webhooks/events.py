"""Webhook event types and their JSON wire form.

An event is a small, provider-agnostic record: an id, a type, when it happened, and a data payload
(the resource snapshot). It is built in the API layer from the changed appointment, queued as JSON,
and later signed and delivered. Serialization is deterministic (sorted keys) so the bytes we sign
are exactly the bytes we send.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from appointments_api.enums import AppointmentStatus


class WebhookEventType(StrEnum):
    APPOINTMENT_CREATED = "appointment.created"
    APPOINTMENT_CONFIRMED = "appointment.confirmed"
    APPOINTMENT_COMPLETED = "appointment.completed"
    APPOINTMENT_CANCELLED = "appointment.cancelled"
    APPOINTMENT_NO_SHOW = "appointment.no_show"


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    id: str
    type: str
    occurred_at: datetime
    data: dict[str, Any]


# Which event a status transition emits. REQUESTED is not here: creation emits APPOINTMENT_CREATED
# explicitly, and no transition targets REQUESTED.
_STATUS_EVENT: dict[AppointmentStatus, WebhookEventType] = {
    AppointmentStatus.CONFIRMED: WebhookEventType.APPOINTMENT_CONFIRMED,
    AppointmentStatus.COMPLETED: WebhookEventType.APPOINTMENT_COMPLETED,
    AppointmentStatus.CANCELLED: WebhookEventType.APPOINTMENT_CANCELLED,
    AppointmentStatus.NO_SHOW: WebhookEventType.APPOINTMENT_NO_SHOW,
}


def event_type_for_status(status: AppointmentStatus) -> WebhookEventType | None:
    """The webhook event a move *into* ``status`` should emit, or None if that status emits none."""
    return _STATUS_EVENT.get(status)


def event_to_dict(event: WebhookEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.type,
        "occurred_at": event.occurred_at.isoformat(),
        "data": event.data,
    }


def event_to_json(event: WebhookEvent) -> str:
    return json.dumps(event_to_dict(event), sort_keys=True, separators=(",", ":"))


def event_from_dict(payload: dict[str, Any]) -> WebhookEvent:
    return WebhookEvent(
        id=payload["id"],
        type=payload["type"],
        occurred_at=datetime.fromisoformat(payload["occurred_at"]),
        data=payload["data"],
    )


def event_from_json(raw: str) -> WebhookEvent:
    parsed: dict[str, Any] = json.loads(raw)
    return event_from_dict(parsed)
