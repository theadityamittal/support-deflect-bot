"""Tests for agent tool interface."""

from __future__ import annotations

import pytest
from agent.tools.base import ToolResult


class TestToolResult:
    def test_success_result(self):
        result = ToolResult.success(data={"key": "value"})
        assert result.ok is True
        assert result.data == {"key": "value"}
        assert result.error is None

    def test_failure_result(self):
        result = ToolResult.failure(error="something broke")
        assert result.ok is False
        assert result.error == "something broke"
        assert result.data == {}

    def test_result_is_frozen(self):
        result = ToolResult.success(data={})
        with pytest.raises(AttributeError):
            result.ok = False  # type: ignore[misc]
