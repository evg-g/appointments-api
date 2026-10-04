"""RFC 9457 ``application/problem+json`` errors and their FastAPI handlers.

Every error the API returns is a *problem document*: a small JSON object with a stable ``type``
URI, a human ``title``, the ``status``, an optional ``detail`` and ``instance`` (the request
path), and, for validation failures, a machine-readable ``errors[]`` list. Clients can branch on
``type`` without string-matching prose. The catalog of types lives in docs/ERROR_CATALOG.md.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from starlette.responses import JSONResponse

from appointments_api.services.errors import (
    CancellationWindowError,
    DomainError,
    IllegalTransitionError,
)
from appointments_api.services.telemetry.errors import DeviceInactiveError, DeviceNotFoundError

PROBLEM_BASE_URI = "https://aurora.example/problems"
PROBLEM_CONTENT_TYPE = "application/problem+json"


class FieldError(BaseModel):
    """One field-level validation failure."""

    field: str
    message: str


class Problem(BaseModel):
    """The problem+json response body (also used as the OpenAPI error schema)."""

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    errors: list[FieldError] | None = None


class APIError(Exception):
    """Base class for errors that map to a problem document.

    Subclasses set ``status``, ``slug`` (the last path segment of the ``type`` URI) and
    ``title``; callers pass an optional ``detail`` and ``errors``.
    """

    status: int = 500
    slug: str = "internal-error"
    title: str = "Internal Server Error"

    def __init__(
        self,
        detail: str | None = None,
        errors: Sequence[FieldError] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.detail = detail
        self.errors = list(errors) if errors else None
        # Extra response headers (e.g. Retry-After / RateLimit-* on a 429). Rendered by _json.
        self.headers = dict(headers) if headers else None
        super().__init__(detail or self.title)

    def to_problem(self, instance: str) -> Problem:
        return Problem(
            type=f"{PROBLEM_BASE_URI}/{self.slug}",
            title=self.title,
            status=self.status,
            detail=self.detail,
            instance=instance,
            errors=self.errors,
        )


class UnauthorizedError(APIError):
    status = 401
    slug = "unauthorized"
    title = "Authentication required"


class ForbiddenError(APIError):
    status = 403
    slug = "forbidden"
    title = "Not allowed"


class NotFoundError(APIError):
    status = 404
    slug = "not-found"
    title = "Resource not found"


class ConflictError(APIError):
    status = 409
    slug = "conflict"
    title = "Conflict"


class SlotUnavailableError(APIError):
    status = 409
    slug = "slot-unavailable"
    title = "Time slot is not available"


class ValidationProblem(APIError):
    status = 422
    slug = "validation-error"
    title = "Request validation failed"


class PreconditionRequiredError(APIError):
    """A mutating request omitted the required ``If-Match`` header (RFC 6585)."""

    status = 428
    slug = "precondition-required"
    title = "If-Match header is required"


class PreconditionFailedError(APIError):
    """The ``If-Match`` ETag did not match the resource's current version (RFC 9110)."""

    status = 412
    slug = "precondition-failed"
    title = "Precondition failed"


class TooManyRequestsError(APIError):
    """The principal exceeded its rate-limit window; carries Retry-After + RateLimit-* headers."""

    status = 429
    slug = "rate-limited"
    title = "Too many requests"


class IdempotencyConflictError(APIError):
    """A request with the same Idempotency-Key is still being processed."""

    status = 409
    slug = "idempotency-conflict"
    title = "Idempotency-Key is already in progress"


class IdempotencyKeyReusedError(APIError):
    """An Idempotency-Key was replayed with a different request body."""

    status = 422
    slug = "idempotency-key-reused"
    title = "Idempotency-Key reused with a different request body"


def _json(problem: Problem, headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status,
        media_type=PROBLEM_CONTENT_TYPE,
        content=problem.model_dump(exclude_none=True),
        headers=dict(headers) if headers else None,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach handlers that render every known error as a problem document."""

    @app.exception_handler(APIError)
    async def _api_error(request: Request, exc: APIError) -> JSONResponse:
        return _json(exc.to_problem(request.url.path), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            FieldError(
                field=".".join(str(p) for p in err["loc"] if p != "body"),
                message=err["msg"],
            )
            for err in exc.errors()
        ]
        problem = ValidationProblem(detail="One or more fields are invalid.", errors=errors)
        return _json(problem.to_problem(request.url.path))

    @app.exception_handler(IllegalTransitionError)
    async def _illegal_transition(request: Request, exc: IllegalTransitionError) -> JSONResponse:
        problem = ConflictError(detail=str(exc))
        return _json(problem.to_problem(request.url.path))

    @app.exception_handler(CancellationWindowError)
    async def _cancellation_window(request: Request, exc: CancellationWindowError) -> JSONResponse:
        problem = ForbiddenError(detail=str(exc))
        return _json(problem.to_problem(request.url.path))

    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        problem = ValidationProblem(detail=str(exc))
        return _json(problem.to_problem(request.url.path))

    @app.exception_handler(DeviceNotFoundError)
    async def _device_not_found(request: Request, exc: DeviceNotFoundError) -> JSONResponse:
        problem = NotFoundError(detail=str(exc))
        return _json(problem.to_problem(request.url.path))

    @app.exception_handler(DeviceInactiveError)
    async def _device_inactive(request: Request, exc: DeviceInactiveError) -> JSONResponse:
        problem = ConflictError(detail=str(exc))
        return _json(problem.to_problem(request.url.path))

    @app.exception_handler(StaleDataError)
    async def _stale_data(request: Request, exc: StaleDataError) -> JSONResponse:
        # Optimistic-lock miss: the row's version moved between our read and our UPDATE. This is
        # the database half of ETag/If-Match — a stale writer is rejected, not silently overwritten.
        problem = PreconditionFailedError(
            detail="The resource was modified by another request; re-read it and retry."
        )
        return _json(problem.to_problem(request.url.path))

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        # The double-booking exclusion constraint surfaces here under a concurrent insert.
        text = str(exc.orig) if exc.orig else str(exc)
        problem: APIError
        if "no_double_booking" in text:
            problem = SlotUnavailableError(
                detail="That time overlaps an existing appointment for this clinician."
            )
        else:
            problem = ConflictError(detail="The change conflicts with existing data.")
        return _json(problem.to_problem(request.url.path))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Catch-all (ADR 0017): any unhandled error, including a failed COMMIT, is a 500 problem
        # document. Starlette still re-raises it after this response, so it is logged as usual.
        problem = APIError(detail="An unexpected error occurred.")
        return _json(problem.to_problem(request.url.path))
