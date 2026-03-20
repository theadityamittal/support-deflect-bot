"""LLM fallback chain: try providers in order until one succeeds.

Chain order from spec: Flash Lite -> Flash -> graceful error.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm.provider import LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class LLMUnavailableError(Exception):
    """All LLM providers in the fallback chain failed."""


class FallbackChain:
    """Tries LLM providers in sequence, falling back on failure.

    Args:
        providers: Ordered list of LLM providers to try.
        model_ids: Corresponding model IDs for each provider.
    """

    def __init__(
        self,
        *,
        providers: list[LLMProvider],
        model_ids: list[str],
    ) -> None:
        if not providers:
            raise ValueError("FallbackChain requires at least one provider")
        if len(providers) != len(model_ids):
            raise ValueError("providers and model_ids must be the same length")

        self._providers = list(providers)
        self._model_ids = list(model_ids)

    def invoke(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int = 1000,
    ) -> LLMResponse:
        """Try each provider in order. Raise LLMUnavailableError if all fail."""
        errors: list[str] = []

        for provider, model_id in zip(self._providers, self._model_ids, strict=True):
            try:
                return provider.invoke(
                    messages=messages,
                    model_id=model_id,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                logger.warning(
                    "LLM provider failed",
                    extra={"model_id": model_id, "error": str(exc)},
                )
                errors.append(f"{model_id}: {exc}")

        raise LLMUnavailableError(
            f"All {len(self._providers)} providers failed: {'; '.join(errors)}"
        )
