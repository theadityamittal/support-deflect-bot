"""LLM model router with two-model split and cost tracking.

Routes reasoning calls to Gemini 2.5 Flash Lite (cheap) and generation
calls to Gemini 2.5 Flash (capable). Tracks cumulative token
usage and estimated cost for budget enforcement.
"""

from __future__ import annotations

from typing import Any

from llm.provider import LLMProvider, LLMResponse, ModelRole

# Pricing per 1M tokens (USD) -- Google Gemini API, paid tier
MODEL_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-flash-lite": {
        "input_per_1m": 0.10,
        "output_per_1m": 0.40,
    },
    "gemini-2.5-flash": {
        "input_per_1m": 0.30,
        "output_per_1m": 2.50,
    },
}

# Default max output tokens per role (from spec)
_DEFAULT_MAX_TOKENS = {
    ModelRole.REASONING: 1000,
    ModelRole.GENERATION: 2000,
}


class LLMRouter:
    """Routes LLM calls by role and tracks cumulative cost.

    Usage:
        router = LLMRouter(provider, reasoning_model, generation_model)
        result = router.invoke(role=ModelRole.REASONING, messages=[...])
        print(router.total_cost)
    """

    def __init__(
        self,
        *,
        provider: LLMProvider,
        reasoning_model_id: str,
        generation_model_id: str,
    ) -> None:
        self._provider = provider
        self._model_map = {
            ModelRole.REASONING: reasoning_model_id,
            ModelRole.GENERATION: generation_model_id,
        }
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cost = 0.0

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._total_output_tokens

    @property
    def total_cost(self) -> float:
        return self._total_cost

    def invoke(
        self,
        *,
        role: ModelRole,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Route an LLM call to the appropriate model and track cost.

        Args:
            role: Whether this is a reasoning or generation call.
            messages: Conversation messages.
            max_tokens: Override default max tokens for this role.

        Returns:
            LLMResponse from the selected model.
        """
        model_id = self._model_map[role]
        effective_max_tokens = max_tokens or _DEFAULT_MAX_TOKENS[role]

        response = self._provider.invoke(
            messages=messages,
            model_id=model_id,
            max_tokens=effective_max_tokens,
        )

        self._total_input_tokens += response.input_tokens
        self._total_output_tokens += response.output_tokens

        pricing = MODEL_PRICING.get(model_id)
        if pricing:
            self._total_cost += response.estimated_cost(
                input_price_per_1m=pricing["input_per_1m"],
                output_price_per_1m=pricing["output_per_1m"],
            )

        return response

    def reset_usage(self) -> None:
        """Reset cumulative usage counters. Call at start of each turn."""
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cost = 0.0
