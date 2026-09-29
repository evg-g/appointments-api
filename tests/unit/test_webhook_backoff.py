"""Unit tests for exponential backoff with jitter. Deterministic via a seeded RNG."""

from __future__ import annotations

from random import Random

import pytest

from appointments_api.services.webhooks.backoff import backoff_delay

BASE = 1.0
CAP = 300.0


@pytest.mark.parametrize("attempt", [0, 1, 2, 3, 5, 8])
def test_delay_is_within_the_equal_jitter_bounds(attempt: int) -> None:
    ceiling = min(CAP, BASE * (2**attempt))
    half = ceiling / 2
    delay = backoff_delay(attempt, base=BASE, cap=CAP, rng=Random(0))
    # Equal jitter: never below half the ceiling, never above the ceiling.
    assert half <= delay <= ceiling


def test_delay_is_capped() -> None:
    # A very high attempt would explode without the cap.
    delay = backoff_delay(20, base=BASE, cap=CAP, rng=Random(1))
    assert delay <= CAP


def test_delay_is_deterministic_for_a_given_seed() -> None:
    assert backoff_delay(3, base=BASE, cap=CAP, rng=Random(42)) == backoff_delay(
        3, base=BASE, cap=CAP, rng=Random(42)
    )


def test_ceiling_grows_with_attempt() -> None:
    # The minimum possible delay (the fixed half) grows with the attempt until the cap.
    low_attempt = backoff_delay(0, base=BASE, cap=CAP, rng=Random(0))
    high_attempt = backoff_delay(4, base=BASE, cap=CAP, rng=Random(0))
    assert high_attempt > low_attempt
