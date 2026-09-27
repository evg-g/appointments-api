"""Exponential backoff with jitter for webhook retries.

A retry storm happens when many clients (or many jobs) all back off by the same amount and then hit
the target together. Jitter spreads them out. We use *equal jitter*: half of the delay is the plain
exponential value, the other half is random within that value — so the wait is never zero, never
more than the exponential ceiling, and no two jobs line up.

The RNG is injected so tests are deterministic.
"""

from __future__ import annotations

from random import Random


def backoff_delay(attempt: int, *, base: float, cap: float, rng: Random) -> float:
    """Seconds to wait before retry number ``attempt`` (0-based: 0 is the first retry).

    The exponential ceiling is ``min(cap, base * 2**attempt)``; the returned delay is the top half
    of that ceiling plus a random amount from the bottom half.
    """
    # 2.0** (not 2**) keeps this a float: int**int is typed as Any because a negative exponent
    # would yield a float.
    ceiling = min(cap, base * (2.0**attempt))
    half = ceiling / 2
    return half + rng.uniform(0, half)
