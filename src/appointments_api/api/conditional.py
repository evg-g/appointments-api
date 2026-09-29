"""ETag / If-Match helpers for optimistic concurrency (RFC 9110).

An appointment carries a ``version`` integer that the database bumps on every UPDATE. We expose
that version as a strong ETag on read and write responses. A client that wants to change the
resource must echo the ETag it last saw in ``If-Match``; if the resource has moved on since, the
write is refused with ``412 Precondition Failed`` instead of silently clobbering someone else's
change.

These functions are pure (no FastAPI, no HTTP) so the matching rules can be unit-tested directly.
The header wiring lives in the router.
"""

from __future__ import annotations


def make_etag(version: int) -> str:
    """Return the strong ETag for a given resource version, e.g. ``version=3`` → ``'"3"'``."""
    return f'"{version}"'


def parse_if_match(header: str | None) -> list[str] | None:
    """Split an ``If-Match`` header into its individual ETag tokens.

    Returns ``None`` when the header is absent (the caller decides whether that is allowed), an
    empty list when it is present but blank, or the list of tokens otherwise. ``*`` is preserved
    as a token so the caller can treat it as "match any existing version".
    """
    if header is None:
        return None
    return [token.strip() for token in header.split(",") if token.strip()]


def if_match_satisfied(header: str | None, current_version: int) -> bool:
    """True if an ``If-Match`` header allows a write against ``current_version``.

    ``*`` matches any existing resource. Otherwise the current ETag must appear among the tokens.
    An absent or blank header is not satisfied — the router turns that into ``428``.
    """
    tokens = parse_if_match(header)
    if not tokens:
        return False
    if "*" in tokens:
        return True
    return make_etag(current_version) in tokens
