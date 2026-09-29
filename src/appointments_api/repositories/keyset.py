"""Shared keyset-pagination query helper for the SQL repositories."""

from __future__ import annotations

from sqlalchemy import Select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from appointments_api.api.pagination import Cursor
from appointments_api.models.base import Base


async def keyset_page[M: Base](
    session: AsyncSession,
    stmt: Select[tuple[M]],
    model: type[M],
    *,
    limit: int,
    cursor: Cursor | None,
) -> tuple[list[M], bool]:
    """Return ``(rows, has_more)`` ordered newest-first, stable under concurrent inserts.

    Ordering is ``(created_at, id)`` descending; the cursor selects rows strictly before the last
    one returned. We fetch ``limit + 1`` rows to know whether a next page exists without a second
    COUNT query.
    """
    ordered = stmt.order_by(model.created_at.desc(), model.id.desc())  # type: ignore[attr-defined]
    if cursor is not None:
        ordered = ordered.where(
            tuple_(model.created_at, model.id) < (cursor.created_at, cursor.id)  # type: ignore[attr-defined]
        )
    ordered = ordered.limit(limit + 1)
    rows = list((await session.execute(ordered)).scalars().all())
    has_more = len(rows) > limit
    return rows[:limit], has_more
