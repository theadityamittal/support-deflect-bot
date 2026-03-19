"""Tests for tool validator middleware."""

from __future__ import annotations

from middleware.agent.tool_validator import validate_tool_call


class TestToolValidator:
    def test_valid_tool_call(self):
        available = {"search_kb", "send_message", "manage_progress"}
        result = validate_tool_call(
            tool_name="search_kb", params={"query": "test"}, available_tools=available
        )
        assert result.valid is True

    def test_unknown_tool_rejected(self):
        available = {"search_kb", "send_message"}
        result = validate_tool_call(
            tool_name="hack_system", params={}, available_tools=available
        )
        assert result.valid is False
        assert "unknown" in result.reason.lower()

    def test_empty_tool_name_rejected(self):
        result = validate_tool_call(
            tool_name="", params={}, available_tools={"search_kb"}
        )
        assert result.valid is False
