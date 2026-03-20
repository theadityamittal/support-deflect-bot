"""Per-turn budget enforcement for LLM and tool calls."""

from __future__ import annotations


class TurnBudgetExceededError(Exception):
    """Raised when a per-turn budget limit is hit."""


class TurnBudgetEnforcer:
    """Tracks and enforces per-turn budgets."""

    def __init__(
        self,
        *,
        max_reasoning_calls: int,
        max_generation_calls: int,
        max_tool_calls: int,
        max_output_tokens: int,
    ) -> None:
        self._max_reasoning = max_reasoning_calls
        self._max_generation = max_generation_calls
        self._max_tool_calls = max_tool_calls
        self._max_output_tokens = max_output_tokens
        self._reasoning_calls = 0
        self._generation_calls = 0
        self._tool_calls = 0
        self._output_tokens = 0

    def record_reasoning_call(self, *, output_tokens: int) -> None:
        self._reasoning_calls += 1
        self._output_tokens += output_tokens

    def record_generation_call(self, *, output_tokens: int) -> None:
        self._generation_calls += 1
        self._output_tokens += output_tokens

    def record_tool_call(self) -> None:
        self._tool_calls += 1

    def check_reasoning_budget(self) -> None:
        if self._reasoning_calls >= self._max_reasoning:
            raise TurnBudgetExceededError(
                f"reasoning call limit reached ({self._max_reasoning})"
            )

    def check_generation_budget(self) -> None:
        if self._generation_calls >= self._max_generation:
            raise TurnBudgetExceededError(
                f"generation call limit reached ({self._max_generation})"
            )

    def check_tool_budget(self) -> None:
        if self._tool_calls >= self._max_tool_calls:
            raise TurnBudgetExceededError(
                f"tool call limit reached ({self._max_tool_calls})"
            )

    def check_token_budget(self) -> None:
        if self._output_tokens >= self._max_output_tokens:
            raise TurnBudgetExceededError(
                f"output token limit reached ({self._output_tokens}/{self._max_output_tokens})"
            )

    def reset(self) -> None:
        self._reasoning_calls = 0
        self._generation_calls = 0
        self._tool_calls = 0
        self._output_tokens = 0
