"""Tests for Gemini LLM provider."""

from unittest.mock import MagicMock, patch

import pytest

from llm.provider import LLMResponse


class TestGeminiProvider:
    def _mock_completion(self, text="Hello", input_tokens=10, output_tokens=5):
        """Create a mock openai chat completion response."""
        choice = MagicMock()
        choice.message.content = text
        response = MagicMock()
        response.choices = [choice]
        response.usage.prompt_tokens = input_tokens
        response.usage.completion_tokens = output_tokens
        return response

    @patch("llm.gemini.OpenAI")
    def test_invoke_returns_llm_response(self, mock_openai_cls):
        from llm.gemini import GeminiProvider

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = self._mock_completion(
            text="The refund policy is...", input_tokens=50, output_tokens=20
        )

        provider = GeminiProvider(api_key="test-key")
        result = provider.invoke(
            messages=[{"role": "user", "content": "What is the refund policy?"}],
            model_id="gemini-2.5-flash-lite",
        )

        assert isinstance(result, LLMResponse)
        assert result.text == "The refund policy is..."
        assert result.input_tokens == 50
        assert result.output_tokens == 20
        assert result.model_id == "gemini-2.5-flash-lite"

    @patch("llm.gemini.OpenAI")
    def test_invoke_passes_max_tokens(self, mock_openai_cls):
        from llm.gemini import GeminiProvider

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = self._mock_completion()

        provider = GeminiProvider(api_key="test-key")
        provider.invoke(
            messages=[{"role": "user", "content": "hi"}],
            model_id="gemini-2.5-flash-lite",
            max_tokens=2000,
        )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["max_tokens"] == 2000

    @patch("llm.gemini.OpenAI")
    def test_invoke_passes_messages_as_is(self, mock_openai_cls):
        from llm.gemini import GeminiProvider

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = self._mock_completion()

        messages = [
            {"role": "system", "content": "You are an onboarding assistant."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "user", "content": "How are you?"},
        ]

        provider = GeminiProvider(api_key="test-key")
        provider.invoke(messages=messages, model_id="gemini-2.5-flash")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["messages"] == messages

    @patch("llm.gemini.OpenAI")
    def test_invoke_raises_on_api_error(self, mock_openai_cls):
        from llm.gemini import GeminiProvider

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception(
            "Gemini unavailable"
        )

        provider = GeminiProvider(api_key="test-key")
        with pytest.raises(Exception, match="Gemini unavailable"):
            provider.invoke(
                messages=[{"role": "user", "content": "hi"}],
                model_id="gemini-2.5-flash-lite",
            )

    @patch("llm.gemini.OpenAI")
    def test_client_created_once(self, mock_openai_cls):
        """Client is reused across invocations (not recreated)."""
        from llm.gemini import GeminiProvider

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = self._mock_completion()

        provider = GeminiProvider(api_key="test-key")
        provider.invoke(messages=[{"role": "user", "content": "a"}], model_id="m")
        provider.invoke(messages=[{"role": "user", "content": "b"}], model_id="m")

        mock_openai_cls.assert_called_once()
