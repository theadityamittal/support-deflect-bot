"""send_message tool — sends a Slack message to the volunteer."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agent.tools.base import AgentTool, ToolResult

if TYPE_CHECKING:
    from slack.client import SlackClient

logger = logging.getLogger(__name__)


class SendMessageTool(AgentTool):
    """Send a message to the volunteer via Slack."""

    def __init__(self, *, slack_client: SlackClient, channel_id: str) -> None:
        self._client = slack_client
        self._channel_id = channel_id

    @property
    def name(self) -> str:
        return "send_message"

    @property
    def description(self) -> str:
        return "Send a message to the volunteer in Slack."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Message text"},
                "blocks": {
                    "type": "array",
                    "description": "Optional Slack Block Kit blocks",
                },
            },
            "required": ["text"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        text = kwargs.get("text", "")
        blocks = kwargs.get("blocks")
        try:
            send_kwargs: dict[str, Any] = {"channel": self._channel_id, "text": text}
            if blocks is not None:
                send_kwargs["blocks"] = blocks
            ts = self._client.send_message(**send_kwargs)
            return ToolResult.success(data={"ts": ts})
        except Exception as e:
            logger.exception("send_message failed")
            return ToolResult.failure(error=f"Failed to send message: {e}")
