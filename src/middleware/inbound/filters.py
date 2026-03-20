# src/middleware/inbound/filters.py
"""CPU-only inbound middleware filters.

These run before any DynamoDB reads (cheapest first).
"""

from __future__ import annotations

from slack.models import EventType, MiddlewareResult, SlackEvent

_ALLOWED_EVENT_TYPES = {EventType.MESSAGE, EventType.APP_MENTION, EventType.TEAM_JOIN}
_ALLOWED_SUBTYPES: set[str | None] = {None}


class EventTypeFilter:
    """Drop events with unrecognized types or message subtypes."""

    @staticmethod
    def check(event: SlackEvent) -> MiddlewareResult:
        if event.event_type not in _ALLOWED_EVENT_TYPES:
            return MiddlewareResult.drop()
        if (
            event.event_type in {EventType.MESSAGE, EventType.APP_MENTION}
            and event.subtype not in _ALLOWED_SUBTYPES
        ):
            return MiddlewareResult.drop()
        return MiddlewareResult.allow()


class BotFilter:
    """Drop messages from bots or the bot's own user ID."""

    def __init__(self, *, bot_user_id: str = "") -> None:
        self._bot_user_id = bot_user_id

    def check(self, event: SlackEvent) -> MiddlewareResult:
        if event.is_bot or (self._bot_user_id and event.user_id == self._bot_user_id):
            return MiddlewareResult.drop()
        return MiddlewareResult.allow()


class EmptyFilter:
    """Drop messages with empty or whitespace-only text."""

    @staticmethod
    def check(event: SlackEvent) -> MiddlewareResult:
        if not event.text or not event.text.strip():
            return MiddlewareResult.drop()
        return MiddlewareResult.allow()
