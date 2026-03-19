"""Tests for output validator middleware."""

from __future__ import annotations

from middleware.agent.output_validator import FALLBACK_MESSAGE, validate_output


class TestOutputValidator:
    def test_valid_output(self):
        result = validate_output("Here's what I found about the events team.")
        assert result == "Here's what I found about the events team."

    def test_empty_output_returns_fallback(self):
        result = validate_output("")
        assert result == FALLBACK_MESSAGE

    def test_none_output_returns_fallback(self):
        result = validate_output(None)
        assert result == FALLBACK_MESSAGE

    def test_excessively_long_output_truncated(self):
        long_text = "a" * 5000
        result = validate_output(long_text)
        assert len(result) <= 4000
