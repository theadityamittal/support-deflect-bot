"""Tests for Slack WebClient wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from slack.client import SlackClient


class TestSlackClient:
    def test_send_message(self):
        mock_web = MagicMock()
        mock_web.chat_postMessage.return_value = {"ok": True, "ts": "123.456"}
        client = SlackClient(web_client=mock_web)
        ts = client.send_message(channel="C1", text="Hello")
        assert ts == "123.456"
        mock_web.chat_postMessage.assert_called_once_with(channel="C1", text="Hello")

    def test_send_message_with_blocks(self):
        mock_web = MagicMock()
        mock_web.chat_postMessage.return_value = {"ok": True, "ts": "123.456"}
        client = SlackClient(web_client=mock_web)
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Hi"}}]
        client.send_message(channel="C1", text="Hi", blocks=blocks)
        mock_web.chat_postMessage.assert_called_once_with(
            channel="C1", text="Hi", blocks=blocks
        )

    def test_send_ephemeral(self):
        mock_web = MagicMock()
        mock_web.chat_postEphemeral.return_value = {"ok": True}
        client = SlackClient(web_client=mock_web)
        client.send_ephemeral(channel="C1", user="U1", text="Only you can see this")
        mock_web.chat_postEphemeral.assert_called_once_with(
            channel="C1", user="U1", text="Only you can see this"
        )

    def test_update_message(self):
        mock_web = MagicMock()
        mock_web.chat_update.return_value = {"ok": True}
        client = SlackClient(web_client=mock_web)
        client.update_message(channel="C1", ts="123.456", text="Updated")
        mock_web.chat_update.assert_called_once_with(
            channel="C1", ts="123.456", text="Updated"
        )

    def test_send_message_in_thread(self):
        mock_web = MagicMock()
        mock_web.chat_postMessage.return_value = {"ok": True, "ts": "123.789"}
        client = SlackClient(web_client=mock_web)
        ts = client.send_message(channel="C1", text="Reply", thread_ts="123.456")
        mock_web.chat_postMessage.assert_called_once_with(
            channel="C1", text="Reply", thread_ts="123.456"
        )
        assert ts == "123.789"


class TestSlackClientNewMethods:
    def test_invite_to_channel_success(self):
        mock_web = MagicMock()
        mock_web.conversations_invite.return_value = {"ok": True}
        client = SlackClient(web_client=mock_web)
        result = client.invite_to_channel(channel_id="C1", user_id="U1")
        assert result is True
        mock_web.conversations_invite.assert_called_once_with(channel="C1", users="U1")

    def test_invite_to_channel_already_member(self):
        from slack_sdk.errors import SlackApiError

        mock_web = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = {"error": "already_in_channel"}
        mock_web.conversations_invite.side_effect = SlackApiError(
            message="already_in_channel", response=mock_resp
        )
        client = SlackClient(web_client=mock_web)
        result = client.invite_to_channel(channel_id="C1", user_id="U1")
        assert result is True

    def test_invite_to_channel_other_error_raises(self):
        from slack_sdk.errors import SlackApiError

        mock_web = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = {"error": "channel_not_found"}
        mock_web.conversations_invite.side_effect = SlackApiError(
            message="channel_not_found", response=mock_resp
        )
        client = SlackClient(web_client=mock_web)
        with pytest.raises(SlackApiError):
            client.invite_to_channel(channel_id="C_BAD", user_id="U1")

    def test_get_user_email_returns_email(self):
        mock_web = MagicMock()
        mock_web.users_info.return_value = {
            "ok": True,
            "user": {"profile": {"email": "user@example.com"}},
        }
        client = SlackClient(web_client=mock_web)
        email = client.get_user_email(user_id="U1")
        assert email == "user@example.com"
        mock_web.users_info.assert_called_once_with(user="U1")

    def test_get_user_email_returns_none_when_unavailable(self):
        mock_web = MagicMock()
        mock_web.users_info.return_value = {
            "ok": True,
            "user": {"profile": {}},
        }
        client = SlackClient(web_client=mock_web)
        email = client.get_user_email(user_id="U1")
        assert email is None

    def test_list_channels_returns_channel_list(self):
        mock_web = MagicMock()
        channels = [{"id": "C1", "name": "general"}, {"id": "C2", "name": "random"}]
        mock_web.conversations_list.return_value = {"ok": True, "channels": channels}
        client = SlackClient(web_client=mock_web)
        result = client.list_channels()
        assert result == channels
        mock_web.conversations_list.assert_called_once_with(types="public_channel")

    def test_list_usergroups_returns_groups(self):
        mock_web = MagicMock()
        groups = [{"id": "S1", "name": "admins"}]
        mock_web.usergroups_list.return_value = {"ok": True, "usergroups": groups}
        client = SlackClient(web_client=mock_web)
        result = client.list_usergroups()
        assert result == groups

    def test_list_usergroups_returns_empty_on_paid_plan_error(self):
        from slack_sdk.errors import SlackApiError

        mock_web = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = {"error": "paid_only"}
        mock_web.usergroups_list.side_effect = SlackApiError(
            message="paid_only", response=mock_resp
        )
        client = SlackClient(web_client=mock_web)
        result = client.list_usergroups()
        assert result == []
