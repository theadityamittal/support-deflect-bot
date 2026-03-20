"""Validate and sanitize final LLM output before sending to user."""

from __future__ import annotations

FALLBACK_MESSAGE = (
    "I'm having trouble processing that right now. "
    "Could you try rephrasing your question?"
)

MAX_OUTPUT_LENGTH = 4000


def validate_output(text: str | None) -> str:
    """Validate LLM output. Returns fallback if invalid."""
    if not text or not text.strip():
        return FALLBACK_MESSAGE

    if len(text) > MAX_OUTPUT_LENGTH:
        return text[:MAX_OUTPUT_LENGTH]

    return text
