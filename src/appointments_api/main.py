"""Application entry point: builds and configures the FastAPI app."""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from appointments_api import __version__
from appointments_api.config import Settings, get_settings


class LivenessResponse(BaseModel):
    """Result of the liveness probe."""

    status: Literal["ok"]
    version: str


class ReadinessResponse(BaseModel):
    """Result of the readiness probe.

    From milestone 3 this also reports the health of the database and Redis. For now the
    process itself being able to answer is the only dependency.
    """

    status: Literal["ok"]
    checks: dict[str, Literal["ok"]]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Accepting ``settings`` makes the app trivially configurable in tests without touching
    the environment.
    """
    settings = settings or get_settings()
    app = FastAPI(
        title="Aurora Clinic — appointments-api",
        version=__version__,
        summary="Appointment scheduling and cold-chain monitoring API.",
    )

    @app.get("/health/live", response_model=LivenessResponse, tags=["health"])
    async def health_live() -> LivenessResponse:
        """Liveness: the process is up and can serve requests."""
        return LivenessResponse(status="ok", version=__version__)

    @app.get("/health/ready", response_model=ReadinessResponse, tags=["health"])
    async def health_ready() -> ReadinessResponse:
        """Readiness: the process and its dependencies are ready for traffic."""
        return ReadinessResponse(status="ok", checks={"process": "ok"})

    return app


app = create_app()
