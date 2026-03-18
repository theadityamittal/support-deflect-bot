# src/middleware/inbound/filters.py
"""CPU-only inbound middleware filters.

These run before any DynamoDB reads (cheapest first).
"""

from __future__ import annotations

from slack.models import EventType, MiddlewareResult, SlackEvent


class BotFilter:
    """Drop messages from bots to prevent self-loops."""

    @staticmethod
    def check(event: SlackEvent) -> MiddlewareResult:
        if event.is_bot:
            return MiddlewareResult.drop()
        return MiddlewareResult.allow()


class EmptyFilter:
    """Drop messages with empty or whitespace-only text.

    team_join events are exempt (they have no text by design).
    """

    @staticmethod
    def check(event: SlackEvent) -> MiddlewareResult:
        if event.event_type == EventType.TEAM_JOIN:
            return MiddlewareResult.allow()
        if not event.text or not event.text.strip():
            return MiddlewareResult.drop()
        return MiddlewareResult.allow()
