"""Callbacks that run once the request's database transaction ends (ADR 0017).

A request-scoped ``TransactionHooks`` holds two lists. ``on_commit`` hooks run only after the
COMMIT succeeds, so side effects outside the database (a webhook event, an idempotency record)
never describe a change that was not saved. ``on_rollback`` hooks run after a rollback, to undo
work done during the request outside the database (for example, release an idempotency key).

Each hook runs on its own: a hook that raises is logged and the rest still run. The transaction
has already ended when hooks run, so a failing hook must not change the response.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog

Hook = Callable[[], Awaitable[None]]

logger = structlog.get_logger(__name__)


class TransactionHooks:
    def __init__(self) -> None:
        self._on_commit: list[Hook] = []
        self._on_rollback: list[Hook] = []

    def on_commit(self, hook: Hook) -> None:
        """Run ``hook`` after the transaction commits. Never runs if it rolls back."""
        self._on_commit.append(hook)

    def on_rollback(self, hook: Hook) -> None:
        """Run ``hook`` after the transaction rolls back. Never runs if it commits."""
        self._on_rollback.append(hook)

    async def run_commit_hooks(self) -> None:
        hooks = self._take()
        await _run_each(hooks[0], phase="on_commit")

    async def run_rollback_hooks(self) -> None:
        hooks = self._take()
        await _run_each(hooks[1], phase="on_rollback")

    def _take(self) -> tuple[list[Hook], list[Hook]]:
        # Clear both lists so each hook runs at most once, whichever way the transaction ended.
        taken = (self._on_commit, self._on_rollback)
        self._on_commit, self._on_rollback = [], []
        return taken


async def _run_each(hooks: list[Hook], *, phase: str) -> None:
    for hook in hooks:
        try:
            await hook()
        except Exception:
            logger.exception("transaction_hook_failed", phase=phase, hook=repr(hook))
