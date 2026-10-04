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
from appointments_api.services.transaction_hooks import TransactionHooks


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True, future=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def create_redis(settings: Settings | None = None) -> Redis:
    settings = settings or get_settings()
    client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return client


def get_transaction_hooks(request: Request) -> TransactionHooks:
    """The request's ``TransactionHooks``, created on first use and shared for the request."""
    hooks: TransactionHooks | None = getattr(request.state, "transaction_hooks", None)
    if hooks is None:
        hooks = TransactionHooks()
        request.state.transaction_hooks = hooks
    return hooks


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on any error.

    After the COMMIT succeeds the request's ``on_commit`` hooks run; after a rollback its
    ``on_rollback`` hooks run (ADR 0017). A failing hook is logged and does not fail the request.
    """
    maker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    hooks = get_transaction_hooks(request)
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            await hooks.run_rollback_hooks()
            raise
    await hooks.run_commit_hooks()


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis
