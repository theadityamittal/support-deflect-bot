"""Ordered inbound middleware chain.

Runs checks cheapest-to-most-expensive:
1. BotFilter (CPU)
2. EmptyFilter (CPU)
3. RateLimiter (1 DynamoDB write)
4. InputSanitizer (CPU + conditional DynamoDB write)
5. TokenBudgetGuard (2 DynamoDB reads)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from middleware.inbound.budget_guard import TokenBudgetGuard
from middleware.inbound.filters import BotFilter, EmptyFilter
from middleware.inbound.rate_limiter import RateLimiter
from middleware.inbound.sanitizer import InputSanitizer
from slack.models import MiddlewareResult, SlackEvent

if TYPE_CHECKING:
    from state.dynamo import DynamoStateStore

logger = logging.getLogger(__name__)


class InboundMiddlewareChain:
    """Run all inbound middleware in order, short-circuiting on failure."""

    def __init__(
        self,
        *,
        state_store: DynamoStateStore,
        max_turns_per_day: int = 50,
        max_monthly_cost: float = 5.0,
        strike_limit: int = 3,
        max_message_length: int = 4000,
    ) -> None:
        self._rate_limiter = RateLimiter(state_store=state_store)
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
        # 1. BotFilter (CPU only)
        result = BotFilter.check(event)
        if not result.allowed:
            return result

        # 2. EmptyFilter (CPU only)
        result = EmptyFilter.check(event)
        if not result.allowed:
            return result

        # 3. RateLimiter (1 DynamoDB conditional write)
        result = self._rate_limiter.check(event)
        if not result.allowed:
            return result

        # 4. InputSanitizer (CPU + conditional DynamoDB write)
        result = self._sanitizer.check(event)
        if not result.allowed:
            return result

        # 5. TokenBudgetGuard (2 DynamoDB reads)
        result = self._budget_guard.check(event)
        if not result.allowed:
            return result

        return MiddlewareResult.allow()
