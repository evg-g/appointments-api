"""Small pure helpers shared by integration tests."""

from __future__ import annotations


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
