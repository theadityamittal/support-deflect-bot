"""Amazon Bedrock LLM provider using the Converse API."""

from __future__ import annotations

from typing import Any

import boto3
from llm.provider import LLMProvider, LLMResponse


class BedrockProvider(LLMProvider):
    """Invokes Amazon Bedrock models via the Converse API.

    Supports any model available through Bedrock Converse
    (Nova Micro, Claude Haiku, etc.).
    """

    def __init__(self, *, region: str = "us-east-1") -> None:
        self._client = boto3.client("bedrock-runtime", region_name=region)

    def invoke(
        self,
        *,
        messages: list[dict[str, Any]],
        model_id: str,
        max_tokens: int = 1000,
    ) -> LLMResponse:
        """Invoke a Bedrock model via the Converse API.

        System messages are extracted and passed via the 'system' parameter.
        User and assistant messages are passed via 'messages'.
        """
        system_prompts, converse_messages = _split_messages(messages)

        kwargs: dict[str, Any] = {
            "modelId": model_id,
            "messages": converse_messages,
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if system_prompts:
            kwargs["system"] = system_prompts

        raw: Any = self._client.converse(**kwargs)

        text: str = raw["output"]["message"]["content"][0]["text"]
        usage: dict[str, int] = raw["usage"]

        return LLMResponse(
            text=text,
            input_tokens=usage["inputTokens"],
            output_tokens=usage["outputTokens"],
            model_id=model_id,
        )


def _split_messages(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate system messages from user/assistant messages.

    Returns:
        (system_prompts, converse_messages) where system_prompts
        is a list of {"text": ...} dicts for the Converse API 'system'
        param, and converse_messages has role + content for 'messages'.
    """
    system_prompts: list[dict[str, Any]] = []
    converse_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg["role"] == "system":
            system_prompts.append({"text": msg["content"]})
        else:
            converse_messages.append(
                {
                    "role": msg["role"],
                    "content": [{"text": msg["content"]}],
                }
            )

    return system_prompts, converse_messages
