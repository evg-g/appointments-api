"""Audit-log writing (ADR 0016).

``record`` writes one entry through an ``AuditWriter``. The writer is a ``typing.Protocol`` so the
service has no database or FastAPI dependency: in the app it is ``AuditLogRepository`` (which writes
in the request's transaction), in unit tests a fake that keeps entries in memory.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from appointments_api.enums import AuditAction


class AuditWriter(Protocol):
    async def add_entry(
        self,
        *,
        actor_id: uuid.UUID | None,
        action: str,
        entity_type: str,
        entity_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None: ...


async def record(
    writer: AuditWriter,
    *,
    actor_id: uuid.UUID | None,
    action: AuditAction,
    entity_type: str,
    entity_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    """Write one audit entry: ``actor_id`` did ``action`` to ``entity_type``/``entity_id``."""
    await writer.add_entry(
        actor_id=actor_id,
        action=action.value,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
    )
