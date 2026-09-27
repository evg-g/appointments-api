"""Application entry point: builds and configures the FastAPI app."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from starlette.requests import Request
from starlette.responses import JSONResponse

from appointments_api import __version__
from appointments_api.api.errors import register_error_handlers
from appointments_api.api.middleware import rate_limit_middleware
from appointments_api.api.routers import (
    appointments,
    auth,
    availability,
    clinicians,
    clinics,
    devices,
    services,
    streams,
    telemetry,
    users,
    webhooks,
)
from appointments_api.config import Settings, get_settings
from appointments_api.db import create_engine, create_redis, create_sessionmaker

API_PREFIX = "/api/v1"


class LivenessResponse(BaseModel):
    """Result of the liveness probe."""

    status: Literal["ok"]
    version: str


class ReadinessResponse(BaseModel):
    """Result of the readiness probe: the process plus each backing dependency."""

    status: Literal["ok", "degraded"]
    checks: dict[str, str]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    The database engine, session factory, and Redis client are created here and stored on
    ``app.state`` so dependencies can reach them. They are created eagerly (no connection is
    opened until first use), and disposed on shutdown via the lifespan.
    """
    settings = settings or get_settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    redis = create_redis(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # The webhook worker can run in-process (off by default; usually a separate process via
        # `python -m appointments_api.workers.webhooks`). When enabled, start it here and stop it
        # cleanly on shutdown.
        worker_task: asyncio.Task[None] | None = None
        stop: asyncio.Event | None = None
        http_client: httpx.AsyncClient | None = None
        if settings.webhook_worker_enabled:
            from appointments_api.workers.webhooks import build_worker

            http_client = httpx.AsyncClient()
            stop = asyncio.Event()
            worker = build_worker(
                redis=redis,
                sessionmaker=sessionmaker,
                http_client=http_client,
                settings=settings,
            )
            worker_task = asyncio.create_task(worker.run(stop))
        try:
            yield
        finally:
            if worker_task is not None and stop is not None:
                stop.set()
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker_task
            if http_client is not None:
                await http_client.aclose()
            await engine.dispose()
            await redis.aclose()

    app = FastAPI(
        title="Aurora Clinic — appointments-api",
        version=__version__,
        summary="Appointment scheduling and cold-chain monitoring API.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.redis = redis

    register_error_handlers(app)

    # Registered first so it runs *inside* the request-id middleware: even a 429 from the limiter
    # still gets an X-Request-ID on the way out.
    app.middleware("http")(rate_limit_middleware)

    @app.middleware("http")
    async def _request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/health/live", response_model=LivenessResponse, tags=["health"])
    async def health_live() -> LivenessResponse:
        """Liveness: the process is up and can serve requests."""
        return LivenessResponse(status="ok", version=__version__)

    @app.get("/health/ready", tags=["health"])
    async def health_ready() -> JSONResponse:
        """Readiness: the process and its dependencies (DB, Redis) are ready for traffic."""
        checks = {"process": "ok"}
        try:
            async with sessionmaker() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "error"
        try:
            await redis.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "error"

        ready = all(value == "ok" for value in checks.values())
        body = ReadinessResponse(status="ok" if ready else "degraded", checks=checks)
        return JSONResponse(status_code=200 if ready else 503, content=body.model_dump())

    for module in (
        auth,
        users,
        clinics,
        clinicians,
        services,
        availability,
        appointments,
        webhooks,
        devices,
        telemetry,
        streams,
    ):
        app.include_router(module.router, prefix=API_PREFIX)

    return app


app = create_app()
