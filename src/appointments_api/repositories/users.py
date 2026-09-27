"""User repository."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from appointments_api.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._s.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self._s.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def add(self, user: User) -> User:
        self._s.add(user)
        await self._s.flush()
        return user
