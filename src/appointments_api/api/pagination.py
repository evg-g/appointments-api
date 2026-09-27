"""Cursor-based (keyset) pagination.

Offset pagination shifts when rows are inserted mid-listing, so page 2 can repeat or skip rows.
Keyset pagination instead remembers the last row's ``(created_at, id)`` and asks for rows strictly
before it, which is stable under concurrent inserts. The cursor is an opaque base64 string so
clients treat it as a token, not a queryable value.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class HasCursorFields(Protocol):
    """A row that can be pointed at by a cursor (has both ordering fields)."""

    id: uuid.UUID
    created_at: datetime


M = TypeVar("M", bound=HasCursorFields)


@dataclass(frozen=True, slots=True)
class Cursor:
    created_at: datetime
    id: uuid.UUID


def encode_cursor(created_at: datetime, id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(value: str) -> Cursor:
    try:
        raw = base64.urlsafe_b64decode(value.encode()).decode()
        iso, id_str = raw.split("|", 1)
        return Cursor(created_at=datetime.fromisoformat(iso), id=uuid.UUID(id_str))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("malformed cursor") from exc


class PageInfo(BaseModel):
    next_cursor: str | None = None
    has_more: bool


class Page(BaseModel, Generic[T]):
    data: list[T]
    page: PageInfo


def build_page(rows: Sequence[M], has_more: bool, to_out: Callable[[M], T]) -> Page[T]:
    """Turn ``(rows, has_more)`` from a repository into a ``Page`` with a next cursor.

    The cursor points at the last returned row, so the next request continues right after it.
    """
    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if (has_more and rows) else None
    return Page(
        data=[to_out(row) for row in rows],
        page=PageInfo(next_cursor=next_cursor, has_more=has_more),
    )
