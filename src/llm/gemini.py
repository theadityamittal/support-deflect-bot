"""Google Gemini LLM provider via OpenAI-compatible endpoint."""

from __future__ import annotations

from typing import Any

from llm.provider import LLMProvider, LLMResponse
from openai import OpenAI

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class GeminiProvider(LLMProvider):
    """Invokes Google Gemini models via the OpenAI-compatible API.

    Uses the openai SDK pointed at Gemini's endpoint.
    Supports Gemini 2.5 Flash Lite (reasoning) and Gemini 2.5 Flash (generation).
    """

    def __init__(self, *, api_key: str) -> None:
        self._client = OpenAI(api_key=api_key, base_url=_GEMINI_BASE_URL)

    def invoke(
        self,
        *,
        messages: list[dict[str, Any]],
        model_id: str,
        max_tokens: int = 1000,
    ) -> LLMResponse:
        """Invoke a Gemini model via the OpenAI-compatible chat completions API.

        Messages (system, user, assistant) are passed through as-is —
        no format translation needed.
        """
        response = self._client.chat.completions.create(
            model=model_id,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=max_tokens,
        )

        choice = response.choices[0]
        usage = response.usage

        return LLMResponse(
            text=choice.message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model_id=model_id,
        )
