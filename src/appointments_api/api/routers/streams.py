"""Server-Sent Events for live telemetry.

``GET /streams/telemetry`` pushes each accepted reading to the web dashboard as it lands. The stream
is backed by a Redis Stream, so reconnection is standard SSE: the browser's ``EventSource`` resends
the last id it saw in the ``Last-Event-ID`` header, and we replay the entries added after it before
following live (spec §5 — "a test for reconnect and Last-Event-ID resumption"). A
``?last_event_id=`` query parameter is also accepted, because a browser cannot set ``Last-Event-ID``
on the *initial* request and some proxies strip it.

Note (documented gap): ``EventSource`` cannot send an ``Authorization`` header, so a production
dashboard fronts this with a cookie/session or a short-lived token in the query. Here the endpoint
uses the normal bearer auth, which the tests drive directly; wiring the browser transport is a
milestone-12 (web) concern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import StreamingResponse

from appointments_api.api.deps import TelemetryStreamDep, require_roles
from appointments_api.enums import UserRole
from appointments_api.models import User
from appointments_api.repositories.redis_telemetry_stream import RedisTelemetryStream

router = APIRouter(prefix="/streams", tags=["streams"])

StaffUser = Annotated[
    User,
    Depends(require_roles(UserRole.CLINICIAN, UserRole.CLINIC_ADMIN, UserRole.PLATFORM_ADMIN)),
]


def _format_event(event_id: str, data: str) -> str:
    return f"id: {event_id}\nevent: reading\ndata: {data}\n\n"


async def _event_source(
    request: Request, stream: RedisTelemetryStream, last_id: str | None
) -> AsyncIterator[str]:
    # Replay what a reconnecting client missed, then follow live from the last replayed id.
    follow_from = "$"
    if last_id:
        for entry_id, data in await stream.replay_since(last_id):
            follow_from = entry_id
            yield _format_event(entry_id, data)
        if follow_from == "$":
            # Nothing to replay (or a bad id): follow everything after the id the client gave us.
            follow_from = last_id
    async for item in stream.follow(follow_from):
        if await request.is_disconnected():
            break
        if item is None:
            yield ": keep-alive\n\n"
            continue
        entry_id, data = item
        yield _format_event(entry_id, data)


@router.get("/telemetry")
async def telemetry_stream(
    request: Request,
    _staff: StaffUser,
    stream: TelemetryStreamDep,
    last_event_id: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    last_id = request.headers.get("last-event-id") or last_event_id
    return StreamingResponse(
        _event_source(request, stream, last_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
