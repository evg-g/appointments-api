"""Audit-log read API.

Admin-only, keyset-paginated, and filterable. The ``audit_log`` table is append-only; this router
never writes. Domain write instrumentation (recording entries on state changes) is tracked
separately — see ``docs/KNOWN_GAPS.md``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from appointments_api.api.deps import AuditLogRepoDep, PageParams, require_roles
from appointments_api.api.pagination import Page, build_page
from appointments_api.api.schemas import AuditLogEntryOut
from appointments_api.enums import UserRole
from appointments_api.models import User

router = APIRouter(prefix="/audit-log", tags=["audit-log"])

AdminUser = Annotated[User, Depends(require_roles(UserRole.CLINIC_ADMIN, UserRole.PLATFORM_ADMIN))]


@router.get("", response_model=Page[AuditLogEntryOut])
async def list_audit_log(
    _admin: AdminUser,
    repo: AuditLogRepoDep,
    page: PageParams,
    actor_id: Annotated[uuid.UUID | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
) -> Page[AuditLogEntryOut]:
    """List audit entries, newest first, with optional actor/action/entity filters."""
    limit, cursor = page
    rows, has_more = await repo.list_entries(
        limit=limit,
        cursor=cursor,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    return build_page(rows, has_more, AuditLogEntryOut.model_validate)
