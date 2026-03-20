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


class TestSendMessageBlocks:
    def test_blocks_type_renders_block_kit(self):
        """blocks_type='calendar_confirmation' resolves to Block Kit blocks."""
        mock_client = MagicMock()
        mock_client.send_message.return_value = "ts-123"
        tool = SendMessageTool(slack_client=mock_client, channel_id="C123")

        blocks_data = {
            "title": "Orientation",
            "date": "2026-03-25",
            "time": "10:00 AM",
            "attendees": ["jane@example.com"],
        }
        result = tool.execute(
            text="Calendar event details",
            blocks_type="calendar_confirmation",
            blocks_data=blocks_data,
        )

        assert result.ok is True
        call_kwargs = mock_client.send_message.call_args.kwargs
        blocks_sent = call_kwargs["blocks"]
        assert isinstance(blocks_sent, list)
        assert len(blocks_sent) > 0
        # Verify block structure contains section with event details
        section_block = blocks_sent[0]
        assert section_block["type"] == "section"
        assert "Orientation" in section_block["text"]["text"]

    def test_blocks_type_none_sends_plain_text(self):
        """When blocks_type is not provided, message sends without Block Kit."""
        mock_client = MagicMock()
        mock_client.send_message.return_value = "ts-456"
        tool = SendMessageTool(slack_client=mock_client, channel_id="C123")

        result = tool.execute(text="Hello volunteer!")

        assert result.ok is True
        call_kwargs = mock_client.send_message.call_args.kwargs
        assert "blocks" not in call_kwargs

    def test_blocks_type_unknown_falls_back_to_plain_text(self):
        """An unrecognised blocks_type logs a warning and sends plain text."""
        mock_client = MagicMock()
        mock_client.send_message.return_value = "ts-789"
        tool = SendMessageTool(slack_client=mock_client, channel_id="C123")

        result = tool.execute(
            text="Fallback text",
            blocks_type="nonexistent_type",
            blocks_data={},
        )

        assert result.ok is True
        call_kwargs = mock_client.send_message.call_args.kwargs
        assert "blocks" not in call_kwargs

    def test_blocks_type_overrides_raw_blocks(self):
        """When both blocks and blocks_type are provided, blocks_type wins."""
        mock_client = MagicMock()
        mock_client.send_message.return_value = "ts-000"
        tool = SendMessageTool(slack_client=mock_client, channel_id="C123")

        raw_blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "raw"}}]
        blocks_data = {
            "title": "Training",
            "date": "2026-03-26",
            "time": "2:00 PM",
            "attendees": [],
        }
        result = tool.execute(
            text="Training event",
            blocks=raw_blocks,
            blocks_type="calendar_confirmation",
            blocks_data=blocks_data,
        )

        assert result.ok is True
        call_kwargs = mock_client.send_message.call_args.kwargs
        # Should use built blocks, not raw_blocks
        sent_blocks = call_kwargs["blocks"]
        assert sent_blocks != raw_blocks
