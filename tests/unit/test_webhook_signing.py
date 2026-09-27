"""Unit tests for webhook HMAC signing and verification. Pure, no I/O."""

from __future__ import annotations

from appointments_api.services.webhooks.signing import sign, signature_header, verify

SECRET = "a-shared-secret-at-least-16-chars"
BODY = b'{"id":"evt_1","type":"appointment.created"}'
TS = 1_800_000_000


def test_sign_is_deterministic() -> None:
    assert sign(SECRET, TS, BODY) == sign(SECRET, TS, BODY)


def test_signature_header_is_prefixed() -> None:
    assert signature_header(SECRET, TS, BODY).startswith("sha256=")


def test_verify_accepts_a_fresh_valid_signature() -> None:
    header = signature_header(SECRET, TS, BODY)
    assert verify(SECRET, TS, BODY, header, now=TS + 5, tolerance_seconds=300)


def test_verify_rejects_a_tampered_body() -> None:
    header = signature_header(SECRET, TS, BODY)
    assert not verify(
        SECRET, TS, b'{"id":"evt_1","amount":999}', header, now=TS, tolerance_seconds=300
    )


def test_verify_rejects_a_wrong_secret() -> None:
    header = signature_header(SECRET, TS, BODY)
    assert not verify("other-secret-16-chars!", TS, BODY, header, now=TS, tolerance_seconds=300)


def test_verify_rejects_a_stale_timestamp() -> None:
    header = signature_header(SECRET, TS, BODY)
    # 10 minutes later, tolerance only 5 minutes → replay window closed.
    assert not verify(SECRET, TS, BODY, header, now=TS + 600, tolerance_seconds=300)
