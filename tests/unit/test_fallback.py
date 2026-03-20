"""Tests for LLM fallback chain."""

from unittest.mock import MagicMock

import pytest
from llm.fallback import FallbackChain, LLMUnavailableError
from llm.provider import LLMResponse


class TestFallbackChain:
    def _make_chain(self, primary_effect=None, fallback_effect=None):
        primary = MagicMock()
        fallback = MagicMock()

        if primary_effect:
            primary.invoke.side_effect = primary_effect
        else:
            primary.invoke.return_value = LLMResponse(
                text="primary", input_tokens=10, output_tokens=5, model_id="primary"
            )

        if fallback_effect:
            fallback.invoke.side_effect = fallback_effect
        else:
            fallback.invoke.return_value = LLMResponse(
                text="fallback", input_tokens=10, output_tokens=5, model_id="fallback"
            )

        return (
            FallbackChain(
                providers=[primary, fallback],
                model_ids=["primary-model", "fallback-model"],
            ),
            primary,
            fallback,
        )

    def test_primary_succeeds(self):
        chain, primary, fallback = self._make_chain()
        result = chain.invoke(messages=[{"role": "user", "content": "hi"}])

        assert result.text == "primary"
        primary.invoke.assert_called_once()
        fallback.invoke.assert_not_called()

    def test_falls_back_on_primary_failure(self):
        chain, primary, fallback = self._make_chain(
            primary_effect=Exception("Provider down"),
        )
        result = chain.invoke(messages=[{"role": "user", "content": "hi"}])

        assert result.text == "fallback"
        fallback.invoke.assert_called_once()

    def test_all_fail_raises_llm_unavailable(self):
        chain, _, _ = self._make_chain(
            primary_effect=Exception("down"),
            fallback_effect=Exception("also down"),
        )

        with pytest.raises(LLMUnavailableError):
            chain.invoke(messages=[{"role": "user", "content": "hi"}])

    def test_passes_max_tokens(self):
        chain, primary, _ = self._make_chain()
        chain.invoke(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=2000,
        )

        call_kwargs = primary.invoke.call_args[1]
        assert call_kwargs["max_tokens"] == 2000

    def test_empty_providers_raises(self):
        with pytest.raises(ValueError, match="at least one provider"):
            FallbackChain(providers=[], model_ids=[])

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError, match="same length"):
            FallbackChain(
                providers=[MagicMock()],
                model_ids=["a", "b"],
            )
