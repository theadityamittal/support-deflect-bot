"""send_message tool — sends a Slack message to the volunteer."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agent.tools.base import AgentTool, ToolResult

if TYPE_CHECKING:
    from slack.client import SlackClient

logger = logging.getLogger(__name__)

# Registry mapping blocks_type strings to builder functions in slack.blocks
_BLOCKS_BUILDERS: dict[str, str] = {
    "calendar_confirmation": "calendar_confirmation",
}


def _build_blocks(
    blocks_type: str, blocks_data: dict[str, Any]
) -> list[dict[str, Any]] | None:
    """Resolve blocks_type to a builder and call it with blocks_data kwargs."""
    import slack.blocks as blk

    builder_name = _BLOCKS_BUILDERS.get(blocks_type)
    if builder_name is None:
        logger.warning("send_message: unknown blocks_type=%r, ignoring", blocks_type)
        return None

    builder = getattr(blk, builder_name, None)
    if builder is None:
        logger.warning(
            "send_message: builder %r not found in slack.blocks", builder_name
        )
        return None

    return builder(**blocks_data)  # type: ignore[no-any-return]


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
                    "description": "Optional Slack Block Kit blocks (raw)",
                },
                "blocks_type": {
                    "type": "string",
                    "description": (
                        "Named block template to render (e.g. 'calendar_confirmation'). "
                        "Requires blocks_data."
                    ),
                },
                "blocks_data": {
                    "type": "object",
                    "description": "Data dict passed to the blocks_type builder.",
                },
            },
            "required": ["text"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        text: str = kwargs.get("text", "")
        raw_blocks: list[dict[str, Any]] | None = kwargs.get("blocks")
        blocks_type: str | None = kwargs.get("blocks_type")
        blocks_data: dict[str, Any] = kwargs.get("blocks_data") or {}

        # Resolve blocks: named template takes priority over raw blocks
        resolved_blocks: list[dict[str, Any]] | None = raw_blocks
        if blocks_type is not None:
            built = _build_blocks(blocks_type, blocks_data)
            if built is not None:
                resolved_blocks = built

        try:
            send_kwargs: dict[str, Any] = {"channel": self._channel_id, "text": text}
            if resolved_blocks is not None:
                send_kwargs["blocks"] = resolved_blocks
            ts = self._client.send_message(**send_kwargs)
            return ToolResult.success(data={"ts": ts})
        except Exception as e:
            logger.exception("send_message failed")
            return ToolResult.failure(error=f"Failed to send message: {e}")
