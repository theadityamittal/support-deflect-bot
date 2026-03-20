"""Tests for LLM model router with cost tracking."""

from unittest.mock import MagicMock

from llm.provider import LLMResponse, ModelRole
from llm.router import MODEL_PRICING, LLMRouter


class TestModelPricing:
    def test_flash_lite_pricing_exists(self):
        assert "gemini-2.5-flash-lite" in MODEL_PRICING

    def test_flash_pricing_exists(self):
        assert "gemini-2.5-flash" in MODEL_PRICING

    def test_pricing_has_required_keys(self):
        for _model_id, pricing in MODEL_PRICING.items():
            assert "input_per_1m" in pricing
            assert "output_per_1m" in pricing


class TestLLMRouter:
    def _make_router(self, mock_provider=None):
        if mock_provider is not None:
            provider = mock_provider
        else:
            provider = MagicMock()
            provider.invoke.return_value = LLMResponse(
                text="answer", input_tokens=100, output_tokens=50, model_id="test"
            )
        return LLMRouter(
            provider=provider,
            reasoning_model_id="gemini-2.5-flash-lite",
            generation_model_id="gemini-2.5-flash",
        )

    def test_reasoning_routes_to_flash_lite(self):
        mock = MagicMock()
        mock.invoke.return_value = LLMResponse(
            text="think",
            input_tokens=10,
            output_tokens=5,
            model_id="gemini-2.5-flash-lite",
        )
        router = self._make_router(mock)

        router.invoke(
            role=ModelRole.REASONING,
            messages=[{"role": "user", "content": "plan next step"}],
        )

        call_kwargs = mock.invoke.call_args[1]
        assert call_kwargs["model_id"] == "gemini-2.5-flash-lite"

    def test_generation_routes_to_flash(self):
        mock = MagicMock()
        mock.invoke.return_value = LLMResponse(
            text="hello",
            input_tokens=10,
            output_tokens=5,
            model_id="gemini-2.5-flash",
        )
        router = self._make_router(mock)

        router.invoke(
            role=ModelRole.GENERATION,
            messages=[{"role": "user", "content": "generate answer"}],
        )

        call_kwargs = mock.invoke.call_args[1]
        assert call_kwargs["model_id"] == "gemini-2.5-flash"

    def test_tracks_cumulative_cost(self):
        mock = MagicMock()
        mock.invoke.return_value = LLMResponse(
            text="x",
            input_tokens=1000,
            output_tokens=500,
            model_id="gemini-2.5-flash-lite",
        )
        router = self._make_router(mock)

        router.invoke(
            role=ModelRole.REASONING,
            messages=[{"role": "user", "content": "a"}],
        )
        router.invoke(
            role=ModelRole.REASONING,
            messages=[{"role": "user", "content": "b"}],
        )

        assert router.total_cost > 0
        assert router.total_input_tokens == 2000
        assert router.total_output_tokens == 1000

    def test_reset_usage(self):
        mock = MagicMock()
        mock.invoke.return_value = LLMResponse(
            text="x", input_tokens=100, output_tokens=50, model_id="test"
        )
        router = self._make_router(mock)

        router.invoke(
            role=ModelRole.REASONING,
            messages=[{"role": "user", "content": "a"}],
        )
        router.reset_usage()

        assert router.total_cost == 0.0
        assert router.total_input_tokens == 0
        assert router.total_output_tokens == 0

    def test_max_tokens_passed_through(self):
        mock = MagicMock()
        mock.invoke.return_value = LLMResponse(
            text="x", input_tokens=10, output_tokens=5, model_id="test"
        )
        router = self._make_router(mock)

        router.invoke(
            role=ModelRole.REASONING,
            messages=[{"role": "user", "content": "a"}],
            max_tokens=2000,
        )

        call_kwargs = mock.invoke.call_args[1]
        assert call_kwargs["max_tokens"] == 2000

    def test_default_max_tokens_by_role(self):
        mock = MagicMock()
        mock.invoke.return_value = LLMResponse(
            text="x", input_tokens=10, output_tokens=5, model_id="test"
        )
        router = self._make_router(mock)

        router.invoke(
            role=ModelRole.REASONING,
            messages=[{"role": "user", "content": "a"}],
        )
        reasoning_max = mock.invoke.call_args[1]["max_tokens"]

        router.invoke(
            role=ModelRole.GENERATION,
            messages=[{"role": "user", "content": "b"}],
        )
        generation_max = mock.invoke.call_args[1]["max_tokens"]

        assert reasoning_max == 1000
        assert generation_max == 2000
