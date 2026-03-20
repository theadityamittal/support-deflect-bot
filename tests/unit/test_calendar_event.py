"""Tests for calendar_event stub tool."""

from __future__ import annotations

from agent.tools.calendar_event import CalendarEventTool


class TestCalendarEventTool:
    def test_name(self):
        tool = CalendarEventTool()
        assert tool.name == "calendar_event"

    def test_stub_returns_success(self):
        tool = CalendarEventTool()
        result = tool.execute(
            title="Orientation meeting",
            date="2026-03-25",
            time="10:00",
            duration_minutes=30,
        )
        assert result.ok is True
        assert result.data["stubbed"] is True
        assert result.data["title"] == "Orientation meeting"

    def test_stub_with_attendee(self):
        tool = CalendarEventTool()
        result = tool.execute(
            title="Training",
            date="2026-03-26",
            time="14:00",
            duration_minutes=60,
            attendee_email="jane@example.com",
        )
        assert result.ok is True
