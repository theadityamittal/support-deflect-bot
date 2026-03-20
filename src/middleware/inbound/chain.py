"""Ordered inbound middleware chain.

Runs checks cheapest-to-most-expensive:
1. EventTypeFilter (CPU — allowlist event types + subtypes)
2. BotFilter (CPU — bot_id + self-ID check)
3. EmptyFilter (CPU — skipped for TEAM_JOIN)
4. ConcurrencyGuard (1 DynamoDB write)
5. InputSanitizer (CPU + conditional DynamoDB write — skipped for TEAM_JOIN)
6. TokenBudgetGuard (2 DynamoDB reads)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from middleware.inbound.budget_guard import TokenBudgetGuard
from middleware.inbound.concurrency_guard import ConcurrencyGuard
from middleware.inbound.filters import BotFilter, EmptyFilter, EventTypeFilter
from middleware.inbound.sanitizer import InputSanitizer
from slack.models import EventType, MiddlewareResult, SlackEvent

if TYPE_CHECKING:
    from state.dynamo import DynamoStateStore

logger = logging.getLogger(__name__)


class InboundMiddlewareChain:
    """Run all inbound middleware in order, short-circuiting on failure."""

    def __init__(
        self,
        *,
        state_store: DynamoStateStore,
        bot_user_id: str = "",
        max_turns_per_day: int = 50,
        max_monthly_cost: float = 5.0,
        strike_limit: int = 3,
        max_message_length: int = 4000,
    ) -> None:
        self._event_type_filter = EventTypeFilter()
        self._bot_filter = BotFilter(bot_user_id=bot_user_id)
        self._concurrency_guard = ConcurrencyGuard(state_store=state_store)
        self._sanitizer = InputSanitizer(
            state_store=state_store,
            strike_limit=strike_limit,
            max_length=max_message_length,
        )
        self._budget_guard = TokenBudgetGuard(
            state_store=state_store,
            max_turns_per_day=max_turns_per_day,
            max_monthly_cost=max_monthly_cost,
        )

    def run(self, event: SlackEvent) -> MiddlewareResult:
        # 1. EventTypeFilter (CPU only)
        result = self._event_type_filter.check(event)
        if not result.allowed:
            return result

        # 2. BotFilter (CPU only)
        result = self._bot_filter.check(event)
        if not result.allowed:
            return result

        # Text-content filters (skip for non-text events like TEAM_JOIN)
        if event.event_type != EventType.TEAM_JOIN:
            # 3. EmptyFilter (CPU only)
            result = EmptyFilter.check(event)
            if not result.allowed:
                return result

        # 4. ConcurrencyGuard (1 DynamoDB conditional write)
        result = self._concurrency_guard.check(event)
        if not result.allowed:
            return result

        # Text-content sanitization (skip for TEAM_JOIN)
        if event.event_type != EventType.TEAM_JOIN:
            # 5. InputSanitizer (CPU + conditional DynamoDB write)
            result = self._sanitizer.check(event)
            if not result.allowed:
                return result

        # 6. TokenBudgetGuard (2 DynamoDB reads)
        result = self._budget_guard.check(event)
        if not result.allowed:
            return result

        return MiddlewareResult.allow()
