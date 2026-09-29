"""Audit-log repository (read side).

The ``audit_log`` table is append-only (see ``models/audit.py``). This repository is the read side:
an admin-facing, keyset-paginated, filterable listing. Filters are optional and combine with AND;
ordering and the cursor come from the shared ``keyset_page`` helper (``(created_at, id)`` DESC).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from appointments_api.api.pagination import Cursor
from appointments_api.models import AuditLogEntry
from appointments_api.repositories.keyset import keyset_page


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

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
