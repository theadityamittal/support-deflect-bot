"""Tests for assign_channel agent tool."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent.tools.assign_channel import AssignChannelTool


class TestAssignChannelTool:
    def test_name(self):
        tool = AssignChannelTool(web_client=MagicMock(), user_id="U123")
        assert tool.name == "assign_channel"

    def test_invites_to_channel(self):
        mock_client = MagicMock()
        mock_client.conversations_invite.return_value = {"ok": True}
        tool = AssignChannelTool(web_client=mock_client, user_id="U123")

        result = tool.execute(channel_id="C456")

        assert result.ok is True
        mock_client.conversations_invite.assert_called_once_with(
            channel="C456", users="U123"
        )

    def test_already_in_channel(self):
        """Slack returns already_in_channel error — tool treats as success."""
        from slack_sdk.errors import SlackApiError

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = {"error": "already_in_channel"}
        mock_client.conversations_invite.side_effect = SlackApiError(
            message="already_in_channel", response=mock_resp
        )
        tool = AssignChannelTool(web_client=mock_client, user_id="U123")

        result = tool.execute(channel_id="C456")

        assert result.ok is True
        assert result.data.get("already_member") is True

    def test_channel_not_found(self):
        from slack_sdk.errors import SlackApiError

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = {"error": "channel_not_found"}
        mock_client.conversations_invite.side_effect = SlackApiError(
            message="channel_not_found", response=mock_resp
        )
        tool = AssignChannelTool(web_client=mock_client, user_id="U123")

        result = tool.execute(channel_id="C_BAD")

        assert result.ok is False
        assert "channel_not_found" in result.error
