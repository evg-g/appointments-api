"""Audit-log repository.

The ``audit_log`` table is append-only (see ``models/audit.py``). The read side is an admin-facing,
keyset-paginated, filterable listing. Filters are optional and combine with AND; ordering and the
cursor come from the shared ``keyset_page`` helper (``(created_at, id)`` DESC). The write side is
``add_entry``, which implements ``services.audit.AuditWriter`` (ADR 0016).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from appointments_api.api.pagination import Cursor
from appointments_api.models import AuditLogEntry
from appointments_api.repositories.keyset import keyset_page


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

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
        """Insert one audit row in the caller's transaction and flush it, so an insert failure
        raises here and the surrounding request rolls back."""
        self._s.add(
            AuditLogEntry(
                actor_id=actor_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                before=before,
                after=after,
            )
        )
        await self._s.flush()

    async def list_entries(
        self,
        *,
        limit: int,
        cursor: Cursor | None,
        actor_id: uuid.UUID | None = None,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> tuple[Sequence[AuditLogEntry], bool]:
        stmt = select(AuditLogEntry)
        if actor_id is not None:
            stmt = stmt.where(AuditLogEntry.actor_id == actor_id)
        if action is not None:
            stmt = stmt.where(AuditLogEntry.action == action)
        if entity_type is not None:
            stmt = stmt.where(AuditLogEntry.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(AuditLogEntry.entity_id == entity_id)
        return await keyset_page(self._s, stmt, AuditLogEntry, limit=limit, cursor=cursor)
