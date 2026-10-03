"""Unit tests for the audit service (ADR 0016). A fake in-memory writer, no I/O."""

from __future__ import annotations

import uuid
from typing import Any

from appointments_api.enums import AuditAction
from appointments_api.services import audit


class FakeAuditWriter:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def add_entry(
        self,
        *,
        actor_id: uuid.UUID | None,
        action: str,
        entity_type: str,
        entity_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        self.entries.append(
            {
                "actor_id": actor_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "before": before,
                "after": after,
            }
        )


def test_action_vocabulary_value() -> None:
    assert AuditAction.APPOINTMENT_CANCELLED.value == "appointment.cancelled"


async def test_record_writes_one_entry_with_every_field() -> None:
    writer = FakeAuditWriter()
    actor = uuid.uuid4()
    entity = str(uuid.uuid4())

    await audit.record(
        writer,
        actor_id=actor,
        action=AuditAction.APPOINTMENT_CANCELLED,
        entity_type="appointment",
        entity_id=entity,
        before={"status": "REQUESTED"},
        after={"status": "CANCELLED"},
    )

    assert writer.entries == [
        {
            "actor_id": actor,
            "action": "appointment.cancelled",
            "entity_type": "appointment",
            "entity_id": entity,
            "before": {"status": "REQUESTED"},
            "after": {"status": "CANCELLED"},
        }
    ]
    # The stored action is the plain string token, not the enum member.
    assert type(writer.entries[0]["action"]) is str
