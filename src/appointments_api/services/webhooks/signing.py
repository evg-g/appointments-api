"""HMAC-SHA256 signing of webhook deliveries (spec §4 rule 8).

Each delivery carries two headers the receiver checks:

- ``X-Webhook-Timestamp`` — when we signed it (epoch seconds).
- ``X-Signature: sha256=<hex>`` — ``HMAC-SHA256(secret, "<timestamp>.<body>")``.

Signing the timestamp *and* the body, and having the receiver reject a timestamp outside a small
tolerance, stops an attacker from replaying a captured-but-valid request later. The receiver uses a
constant-time comparison so it cannot be probed byte by byte.
"""

from __future__ import annotations

import hashlib
import hmac


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """The raw hex HMAC over ``"<timestamp>.<body>"``."""
    signed_payload = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()


def signature_header(secret: str, timestamp: int, body: bytes) -> str:
    """The value for the ``X-Signature`` header, e.g. ``sha256=ab12...``."""
    return f"sha256={sign(secret, timestamp, body)}"


def verify(
    secret: str,
    timestamp: int,
    body: bytes,
    header: str,
    *,
    now: int,
    tolerance_seconds: int,
) -> bool:
    """True if ``header`` is a valid, fresh signature for ``body``.

    Rejects a timestamp further than ``tolerance_seconds`` from ``now`` (replay protection), then
    compares signatures in constant time.
    """
    if abs(now - timestamp) > tolerance_seconds:
        return False
    expected = signature_header(secret, timestamp, body)
    return hmac.compare_digest(expected, header)
