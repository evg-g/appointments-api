"""Fixtures for the security tier.

Security tests exercise the real app end-to-end (auth, RBAC, validation, persistence), so they use
the integration containers and the async ``client`` exactly like the integration tier — no doubles.
"""

from __future__ import annotations

from typing import Any

import pytest

from appointments_api.enums import UserRole
from tests.integration.conftest import *  # noqa: F403  (shared container/app/client fixtures)

# ``import *`` skips the underscore-prefixed autouse migration fixture; pull it in explicitly.
from tests.integration.conftest import _migrate  # noqa: F401

ALL_ROLES: tuple[str, ...] = tuple(role.value for role in UserRole)


@pytest.fixture
async def role_tokens(seed: Any, login: Any) -> dict[str, str]:
    """A valid access token for one user of each role, keyed by role name."""
    tokens: dict[str, str] = {}
    for role in ALL_ROLES:
        email = f"{role.lower()}@authz.io"
        await seed.user(role=role, email=email)
        tokens[role] = await login(email)
    return tokens
