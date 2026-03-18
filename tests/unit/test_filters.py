# tests/unit/test_filters.py
"""Tests for BotFilter and EmptyFilter middleware."""

from __future__ import annotations

from middleware.inbound.filters import BotFilter, EmptyFilter
from slack.models import EventType, SlackEvent


class TestBotFilter:
    def test_drops_bot_messages(self):
        event = SlackEvent(
            event_id="Ev001",
            workspace_id="W1",
            user_id="U1",
            channel_id="C1",
            text="bot msg",
            event_type=EventType.MESSAGE,
            timestamp="123",
            is_bot=True,
        )
        result = BotFilter.check(event)
        assert result.allowed is False
        assert result.should_respond is False

    def test_allows_human_messages(self):
        event = SlackEvent(
            event_id="Ev001",
            workspace_id="W1",
            user_id="U1",
            channel_id="C1",
            text="human msg",
            event_type=EventType.MESSAGE,
            timestamp="123",
            is_bot=False,
        )
        result = BotFilter.check(event)
        assert result.allowed is True

    def test_allows_team_join(self):
        event = SlackEvent(
            event_id="Ev001",
            workspace_id="W1",
            user_id="U1",
            channel_id="",
            text="",
            event_type=EventType.TEAM_JOIN,
            timestamp="123",
            is_bot=False,
        )
        result = BotFilter.check(event)
        assert result.allowed is True


class TestEmptyFilter:
    def test_drops_empty_text(self):
        event = SlackEvent(
            event_id="Ev001",
            workspace_id="W1",
            user_id="U1",
            channel_id="C1",
            text="",
            event_type=EventType.MESSAGE,
            timestamp="123",
        )
        result = EmptyFilter.check(event)
        assert result.allowed is False
        assert result.should_respond is False

    def test_drops_whitespace_only(self):
        event = SlackEvent(
            event_id="Ev001",
            workspace_id="W1",
            user_id="U1",
            channel_id="C1",
            text="   \n\t  ",
            event_type=EventType.MESSAGE,
            timestamp="123",
        )
        result = EmptyFilter.check(event)
        assert result.allowed is False

    def test_allows_non_empty_text(self):
        event = SlackEvent(
            event_id="Ev001",
            workspace_id="W1",
            user_id="U1",
            channel_id="C1",
            text="Hello",
            event_type=EventType.MESSAGE,
            timestamp="123",
        )
        result = EmptyFilter.check(event)
        assert result.allowed is True

    def test_allows_team_join_with_empty_text(self):
        """team_join events have no text but should not be dropped."""
        event = SlackEvent(
            event_id="Ev001",
            workspace_id="W1",
            user_id="U1",
            channel_id="",
            text="",
            event_type=EventType.TEAM_JOIN,
            timestamp="123",
        )
        result = EmptyFilter.check(event)
        assert result.allowed is True
