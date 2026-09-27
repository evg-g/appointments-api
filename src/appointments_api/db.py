"""Async database and Redis wiring.

The engine, session factory, and Redis client are created once per app and stored on
``app.state`` (see ``main.create_app``). Routers reach them through the ``get_session`` and
``get_redis`` FastAPI dependencies, so tests can swap in a testcontainer-backed engine by simply
building the app with different settings — no globals to patch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from starlette.requests import Request

from appointments_api.config import Settings, get_settings


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True, future=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def create_redis(settings: Settings | None = None) -> Redis:
    settings = settings or get_settings()
    client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return client


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on any error."""
    maker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis
