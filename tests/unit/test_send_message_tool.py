"""Tests for send_message agent tool."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent.tools.send_message import SendMessageTool


class TestSendMessageTool:
    def test_name(self):
        tool = SendMessageTool(slack_client=MagicMock(), channel_id="C123")
        assert tool.name == "send_message"

    def test_sends_message(self):
        mock_client = MagicMock()
        mock_client.send_message.return_value = "1234567890.123456"
        tool = SendMessageTool(slack_client=mock_client, channel_id="C123")

        result = tool.execute(text="Hello volunteer!")

        assert result.ok is True
        assert result.data["ts"] == "1234567890.123456"
        mock_client.send_message.assert_called_once_with(
            channel="C123", text="Hello volunteer!"
        )

    def test_sends_with_blocks(self):
        mock_client = MagicMock()
        mock_client.send_message.return_value = "ts"
        tool = SendMessageTool(slack_client=mock_client, channel_id="C123")
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]

        result = tool.execute(text="hi", blocks=blocks)

        assert result.ok is True
        mock_client.send_message.assert_called_once_with(
            channel="C123", text="hi", blocks=blocks
        )

    def test_handles_error(self):
        mock_client = MagicMock()
        mock_client.send_message.side_effect = Exception("Slack API error")
        tool = SendMessageTool(slack_client=mock_client, channel_id="C123")

        result = tool.execute(text="hello")

        assert result.ok is False
        assert "Slack API error" in result.error
