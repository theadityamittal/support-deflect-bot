"""Tests for LLM provider interface."""

import pytest

from llm.provider import LLMProvider, LLMResponse, ModelRole


class TestModelRole:
    def test_role_values(self):
        assert ModelRole.REASONING.value == "reasoning"
        assert ModelRole.GENERATION.value == "generation"


class TestLLMProvider:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            LLMProvider()

    def test_concrete_implementation(self):
        class FakeProvider(LLMProvider):
            def invoke(self, *, messages, model_id, max_tokens=1000):
                return LLMResponse(
                    text="hello",
                    input_tokens=10,
                    output_tokens=5,
                    model_id=model_id,
                )

        provider = FakeProvider()
        result = provider.invoke(
            messages=[{"role": "user", "content": "hi"}],
            model_id="fake-model",
        )
        assert result.text == "hello"
        assert result.input_tokens == 10
        assert result.output_tokens == 5

    def test_llm_response_is_frozen(self):
        resp = LLMResponse(text="hi", input_tokens=1, output_tokens=1, model_id="x")
        with pytest.raises(AttributeError):
            resp.text = "modified"

    def test_llm_response_estimated_cost(self):
        resp = LLMResponse(
            text="hi",
            input_tokens=1000,
            output_tokens=500,
            model_id="gemini-2.5-flash-lite",
        )
        cost = resp.estimated_cost(input_price_per_1m=0.035, output_price_per_1m=0.14)
        assert cost > 0
        assert isinstance(cost, float)
