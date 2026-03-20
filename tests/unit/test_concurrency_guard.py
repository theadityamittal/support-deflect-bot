"""Tests for per-user concurrency guard (processing lock)."""

from __future__ import annotations

from unittest.mock import MagicMock

from middleware.inbound.concurrency_guard import ConcurrencyGuard
from slack.models import EventType, SlackEvent


def _make_event(user_id: str = "U1", workspace_id: str = "W1") -> SlackEvent:
    return SlackEvent(
        event_id="Ev001",
        workspace_id=workspace_id,
        user_id=user_id,
        channel_id="C1",
        text="hello",
        event_type=EventType.MESSAGE,
        timestamp="123",
    )


class TestConcurrencyGuard:
    def test_allows_when_lock_acquired(self):
        mock_store = MagicMock()
        mock_store.acquire_lock.return_value = True
        limiter = ConcurrencyGuard(state_store=mock_store)
        result = limiter.check(_make_event())
        assert result.allowed is True
        mock_store.acquire_lock.assert_called_once_with(workspace_id="W1", user_id="U1")

    def test_rejects_when_lock_held(self):
        mock_store = MagicMock()
        mock_store.acquire_lock.return_value = False
        limiter = ConcurrencyGuard(state_store=mock_store)
        result = limiter.check(_make_event())
        assert result.allowed is False
        assert "still working" in result.reason.lower()

    def test_rejects_with_respond_flag(self):
        mock_store = MagicMock()
        mock_store.acquire_lock.return_value = False
        limiter = ConcurrencyGuard(state_store=mock_store)
        result = limiter.check(_make_event())
        assert result.should_respond is True
