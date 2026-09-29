"""HTTP middleware: per-principal rate limiting.

This runs on every request, identifies the principal (the access token's subject, or the client IP
for unauthenticated calls), and counts the request against a fixed window in Redis. Allowed
responses carry ``RateLimit-*`` headers so a well-behaved client can back off before being blocked;
a blocked request gets ``429`` as problem+json with ``Retry-After``.

It is deliberately **fail-open**: if the Redis counter is unavailable, the request is allowed rather
than the whole API going dark because the limiter's backend hiccuped.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from appointments_api.api.errors import PROBLEM_CONTENT_TYPE, TooManyRequestsError
from appointments_api.config import Settings, get_settings
from appointments_api.repositories.redis_rate_limit import RedisRateLimitStore
from appointments_api.security import TokenError, decode_access_token
from appointments_api.services.rate_limit import FixedWindowRateLimiter, RateLimitDecision

CallNext = Callable[[Request], Awaitable[Response]]

# Paths that must never be rate limited: probes and the API docs.
_EXEMPT_PREFIXES = ("/health",)
_EXEMPT_EXACT = frozenset({"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"})


def _is_exempt(path: str) -> bool:
    return path in _EXEMPT_EXACT or any(path.startswith(p) for p in _EXEMPT_PREFIXES)


def _principal(request: Request) -> str:
    """Identify the caller: the token subject if present and valid, else the client IP.

    The token is verified with the same ``get_settings()`` the auth layer uses to sign and check
    it, so the middleware's view of "who is this" always agrees with ``get_current_user``.
    """
    authorization = request.headers.get("Authorization")
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[len("bearer ") :].strip()
        try:
            claims = decode_access_token(token, settings=get_settings())
        except TokenError:
            pass
        else:
            return f"user:{claims['sub']}"
    host = request.client.host if request.client else "anonymous"
    return f"ip:{host}"


def _headers(decision: RateLimitDecision) -> dict[str, str]:
    return {
        "RateLimit-Limit": str(decision.limit),
        "RateLimit-Remaining": str(decision.remaining),
        "RateLimit-Reset": str(decision.reset_seconds),
    }


async def rate_limit_middleware(request: Request, call_next: CallNext) -> Response:
    settings: Settings = request.app.state.settings
    if not settings.rate_limit_enabled or _is_exempt(request.url.path):
        return await call_next(request)

    redis: Redis = request.app.state.redis
    limiter = FixedWindowRateLimiter(
        RedisRateLimitStore(redis),
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )

    try:
        decision = await limiter.check(_principal(request))
    except Exception:
        # Fail open: never let a limiter-backend failure take down the API.
        return await call_next(request)

    if not decision.allowed:
        problem = TooManyRequestsError(
            detail="Rate limit exceeded; slow down and retry after the indicated delay."
        )
        headers = {**_headers(decision), "Retry-After": str(decision.retry_after)}
        return JSONResponse(
            status_code=problem.status,
            media_type=PROBLEM_CONTENT_TYPE,
            content=problem.to_problem(request.url.path).model_dump(exclude_none=True),
            headers=headers,
        )

    response = await call_next(request)
    for name, value in _headers(decision).items():
        response.headers[name] = value
    return response
