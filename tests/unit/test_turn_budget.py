"""Tests for turn budget middleware."""

from __future__ import annotations

import pytest

from middleware.agent.turn_budget import TurnBudgetEnforcer, TurnBudgetExceededError


class TestTurnBudgetEnforcer:
    def test_allows_within_budget(self):
        budget = TurnBudgetEnforcer(
            max_reasoning_calls=3,
            max_generation_calls=1,
            max_tool_calls=4,
            max_output_tokens=5000,
        )
        budget.record_reasoning_call(output_tokens=500)
        budget.record_reasoning_call(output_tokens=500)
        # No exception — within budget

    def test_rejects_over_reasoning_limit(self):
        budget = TurnBudgetEnforcer(
            max_reasoning_calls=2,
            max_generation_calls=1,
            max_tool_calls=4,
            max_output_tokens=5000,
        )
        budget.record_reasoning_call(output_tokens=100)
        budget.record_reasoning_call(output_tokens=100)
        with pytest.raises(TurnBudgetExceededError, match="reasoning"):
            budget.check_reasoning_budget()

    def test_rejects_over_token_limit(self):
        budget = TurnBudgetEnforcer(
            max_reasoning_calls=3,
            max_generation_calls=1,
            max_tool_calls=4,
            max_output_tokens=1000,
        )
        budget.record_reasoning_call(output_tokens=800)
        budget.record_reasoning_call(output_tokens=300)
        with pytest.raises(TurnBudgetExceededError, match="token"):
            budget.check_token_budget()

    def test_rejects_over_tool_limit(self):
        budget = TurnBudgetEnforcer(
            max_reasoning_calls=3,
            max_generation_calls=1,
            max_tool_calls=2,
            max_output_tokens=5000,
        )
        budget.record_tool_call()
        budget.record_tool_call()
        with pytest.raises(TurnBudgetExceededError, match="tool"):
            budget.check_tool_budget()

    def test_reset(self):
        budget = TurnBudgetEnforcer(
            max_reasoning_calls=1,
            max_generation_calls=1,
            max_tool_calls=1,
            max_output_tokens=100,
        )
        budget.record_reasoning_call(output_tokens=50)
        budget.record_tool_call()
        budget.reset()
        # Should not raise after reset
        budget.check_reasoning_budget()
        budget.check_tool_budget()
