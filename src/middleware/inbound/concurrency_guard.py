"""Per-user processing lock middleware.

Uses DynamoDB conditional write with 60s TTL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from slack.models import MiddlewareResult, SlackEvent

if TYPE_CHECKING:
    from state.dynamo import DynamoStateStore


class ConcurrencyGuard:
    """Check and acquire per-user processing lock."""

    def __init__(self, *, state_store: DynamoStateStore) -> None:
        self._store = state_store

    def check(self, event: SlackEvent) -> MiddlewareResult:
        acquired = self._store.acquire_lock(
            workspace_id=event.workspace_id,
            user_id=event.user_id,
        )
        if not acquired:
            return MiddlewareResult.reject("Still working on your previous message...")
        return MiddlewareResult.allow()
