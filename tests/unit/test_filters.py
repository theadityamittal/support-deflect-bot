# tests/unit/test_filters.py
"""Tests for EventTypeFilter, BotFilter, and EmptyFilter middleware."""

from __future__ import annotations

from middleware.inbound.filters import BotFilter, EmptyFilter, EventTypeFilter
from slack.models import EventType, SlackEvent


def _make_event(
    *,
    event_type: EventType = EventType.MESSAGE,
    subtype: str | None = None,
    is_bot: bool = False,
    user_id: str = "U1",
    text: str = "hello",
) -> SlackEvent:
    return SlackEvent(
        event_id="Ev001",
        workspace_id="W1",
        user_id=user_id,
        channel_id="C1",
        text=text,
        event_type=event_type,
        timestamp="123",
        is_bot=is_bot,
        subtype=subtype,
    )


class TestEventTypeFilter:
    def test_allows_plain_message(self):
        result = EventTypeFilter.check(_make_event())
        assert result.allowed is True

    def test_allows_app_mention(self):
        result = EventTypeFilter.check(_make_event(event_type=EventType.APP_MENTION))
        assert result.allowed is True

    def test_allows_team_join(self):
        result = EventTypeFilter.check(_make_event(event_type=EventType.TEAM_JOIN))
        assert result.allowed is True

    def test_drops_command_event_type(self):
        result = EventTypeFilter.check(_make_event(event_type=EventType.COMMAND))
        assert result.allowed is False
        assert result.should_respond is False

    def test_drops_message_with_subtype(self):
        result = EventTypeFilter.check(_make_event(subtype="channel_join"))
        assert result.allowed is False
        assert result.should_respond is False

    def test_drops_message_changed_subtype(self):
        result = EventTypeFilter.check(_make_event(subtype="message_changed"))
        assert result.allowed is False

    def test_drops_bot_message_subtype(self):
        result = EventTypeFilter.check(_make_event(subtype="bot_message"))
        assert result.allowed is False


class TestBotFilter:
    def test_drops_bot_messages(self):
        bot_filter = BotFilter(bot_user_id="B_SELF")
        result = bot_filter.check(_make_event(is_bot=True))
        assert result.allowed is False
        assert result.should_respond is False

    def test_allows_human_messages(self):
        bot_filter = BotFilter(bot_user_id="B_SELF")
        result = bot_filter.check(_make_event())
        assert result.allowed is True

    def test_allows_team_join(self):
        bot_filter = BotFilter(bot_user_id="B_SELF")
        result = bot_filter.check(_make_event(event_type=EventType.TEAM_JOIN, text=""))
        assert result.allowed is True

    def test_drops_own_user_id(self):
        bot_filter = BotFilter(bot_user_id="B_SELF")
        result = bot_filter.check(_make_event(user_id="B_SELF", is_bot=False))
        assert result.allowed is False
        assert result.should_respond is False

    def test_allows_different_user_id(self):
        bot_filter = BotFilter(bot_user_id="B_SELF")
        result = bot_filter.check(_make_event(user_id="U_OTHER"))
        assert result.allowed is True


class TestEmptyFilter:
    def test_drops_empty_text(self):
        result = EmptyFilter.check(_make_event(text=""))
        assert result.allowed is False
        assert result.should_respond is False

    def test_drops_whitespace_only(self):
        result = EmptyFilter.check(_make_event(text="   \n\t  "))
        assert result.allowed is False

    def test_allows_non_empty_text(self):
        result = EmptyFilter.check(_make_event(text="Hello"))
        assert result.allowed is True

    def test_drops_team_join_with_empty_text(self):
        """EmptyFilter drops empty text regardless of event type.

        TEAM_JOIN bypass is handled by the chain, not individual filters.
        """
        result = EmptyFilter.check(_make_event(event_type=EventType.TEAM_JOIN, text=""))
        assert result.allowed is False
