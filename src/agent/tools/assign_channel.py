"""assign_channel tool — invites user to Slack channels."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agent.tools.base import AgentTool, ToolResult

if TYPE_CHECKING:
    from slack_sdk import WebClient

logger = logging.getLogger(__name__)


class AssignChannelTool(AgentTool):
    """Invite the volunteer to a Slack channel. Idempotent."""

    def __init__(self, *, web_client: WebClient, user_id: str) -> None:
        self._client = web_client
        self._user_id = user_id

    @property
    def name(self) -> str:
        return "assign_channel"

    @property
    def description(self) -> str:
        return "Invite the volunteer to a Slack channel."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "Slack channel ID to invite user to",
                }
            },
            "required": ["channel_id"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        channel_id = kwargs.get("channel_id", "")
        try:
            self._client.conversations_invite(channel=channel_id, users=self._user_id)
            return ToolResult.success(data={"channel_id": channel_id, "invited": True})
        except Exception as e:
            error_msg = str(e)
            # Slack returns already_in_channel — treat as success
            if "already_in_channel" in error_msg:
                return ToolResult.success(
                    data={"channel_id": channel_id, "already_member": True}
                )
            logger.exception("assign_channel failed for %s", channel_id)
            return ToolResult.failure(error=f"Channel assignment failed: {error_msg}")
