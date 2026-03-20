# tests/unit/test_slack_handler.py
"""Tests for the Slack Handler Lambda entry point."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from slack.handler import (
    _build_middleware_chain,
    _send_ephemeral_rejection,
    lambda_handler,
)

_EVENT_BODY = {
    "type": "event_callback",
    "event": {
        "type": "message",
        "user": "U123",
        "channel": "C789",
        "text": "Hello",
        "ts": "123.456",
        "event_ts": "123.456",
    },
    "event_id": "Ev001",
    "team_id": "W456",
}


def _make_api_gw_event(
    path: str,
    body: dict,
    method: str = "POST",
    headers: dict | None = None,
) -> dict:
    return {
        "path": path,
        "httpMethod": method,
        "headers": headers or {},
        "body": json.dumps(body),
        "requestContext": {},
    }


class TestSlackHandlerLambda:
    @patch("slack.handler._get_signing_secret")
    @patch("slack.handler.verify_slack_signature")
    def test_url_verification_challenge(self, mock_verify, mock_secret):
        """Slack URL verification returns the challenge token."""
        mock_secret.return_value = "secret"
        event = _make_api_gw_event(
            path="/slack/events",
            body={"type": "url_verification", "challenge": "abc123"},
        )
        result = lambda_handler(event, {})
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["challenge"] == "abc123"

    @patch("slack.handler._get_signing_secret")
    @patch("slack.handler.verify_slack_signature")
    @patch("slack.handler._enqueue_to_sqs")
    @patch("slack.handler._build_middleware_chain")
    def test_event_passes_middleware_and_enqueues(
        self, mock_chain_builder, mock_enqueue, mock_verify, mock_secret
    ):
        mock_secret.return_value = "secret"
        mock_chain = MagicMock()
        mock_chain.run.return_value = MagicMock(allowed=True)
        mock_chain_builder.return_value = mock_chain

        event = _make_api_gw_event(path="/slack/events", body=_EVENT_BODY)
        result = lambda_handler(event, {})
        assert result["statusCode"] == 200
        mock_enqueue.assert_called_once()

    @patch("slack.handler._get_signing_secret")
    @patch("slack.handler.verify_slack_signature")
    @patch("slack.handler._send_ephemeral_rejection")
    @patch("slack.handler._build_middleware_chain")
    def test_reject_sends_ephemeral(
        self, mock_chain_builder, mock_ephemeral, mock_verify, mock_secret
    ):
        """When middleware rejects with should_respond=True, send ephemeral."""
        mock_secret.return_value = "secret"
        mock_chain = MagicMock()
        mock_chain.run.return_value = MagicMock(
            allowed=False, should_respond=True, reason="Still working..."
        )
        mock_chain_builder.return_value = mock_chain

        event = _make_api_gw_event(path="/slack/events", body=_EVENT_BODY)
        result = lambda_handler(event, {})
        assert result["statusCode"] == 200
        mock_ephemeral.assert_called_once_with(
            workspace_id="W456",
            channel_id="C789",
            user_id="U123",
            text="Still working...",
        )

    @patch("slack.handler._get_signing_secret")
    @patch("slack.handler.verify_slack_signature")
    @patch("slack.handler._send_ephemeral_rejection")
    @patch("slack.handler._build_middleware_chain")
    def test_drop_does_not_send_ephemeral(
        self, mock_chain_builder, mock_ephemeral, mock_verify, mock_secret
    ):
        """When middleware drops (should_respond=False), no ephemeral sent."""
        mock_secret.return_value = "secret"
        mock_chain = MagicMock()
        mock_chain.run.return_value = MagicMock(
            allowed=False, should_respond=False, reason=None
        )
        mock_chain_builder.return_value = mock_chain

        event = _make_api_gw_event(path="/slack/events", body=_EVENT_BODY)
        result = lambda_handler(event, {})
        assert result["statusCode"] == 200
        mock_ephemeral.assert_not_called()

    @patch("slack.handler._get_signing_secret")
    @patch("slack.handler.verify_slack_signature")
    @patch("slack.handler._get_state_store")
    def test_slash_command_routed(self, mock_get_store, mock_verify, mock_secret):
        mock_secret.return_value = "secret"
        mock_get_store.return_value = MagicMock()
        event = _make_api_gw_event(
            path="/slack/commands",
            body={
                "command": "/onboard-help",
                "user_id": "U123",
                "team_id": "W456",
                "channel_id": "C789",
                "trigger_id": "T001",
                "text": "",
                "response_url": "https://hooks.slack.com/commands/xxx",
            },
        )
        result = lambda_handler(event, {})
        assert result["statusCode"] == 200

    @patch("slack.handler._get_signing_secret")
    @patch("slack.handler.verify_slack_signature")
    def test_invalid_signature_returns_401(self, mock_verify, mock_secret):
        from slack.signature import InvalidSignatureError

        mock_secret.return_value = "secret"
        mock_verify.side_effect = InvalidSignatureError("bad sig")
        event = _make_api_gw_event(path="/slack/events", body=_EVENT_BODY)
        result = lambda_handler(event, {})
        assert result["statusCode"] == 401

    @patch("slack.handler._get_signing_secret")
    @patch("slack.handler.verify_slack_signature")
    def test_interaction_path_returns_200(self, mock_verify, mock_secret):
        mock_secret.return_value = "secret"
        event = _make_api_gw_event(
            path="/slack/interactions", body={"type": "block_actions"}
        )
        result = lambda_handler(event, {})
        assert result["statusCode"] == 200


class TestBuildMiddlewareChain:
    @patch("slack.handler._get_state_store")
    def test_passes_bot_user_id_from_config(self, mock_get_store):
        mock_store = MagicMock()
        mock_config = MagicMock()
        mock_config.bot_user_id = "B_BOT"
        mock_store.get_workspace_config.return_value = mock_config
        mock_get_store.return_value = mock_store

        chain = _build_middleware_chain(workspace_id="W1")
        assert chain._bot_filter._bot_user_id == "B_BOT"

    @patch("slack.handler._get_state_store")
    def test_defaults_bot_user_id_when_no_config(self, mock_get_store):
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = None
        mock_get_store.return_value = mock_store

        chain = _build_middleware_chain(workspace_id="W1")
        assert chain._bot_filter._bot_user_id == ""


class TestSendEphemeralRejection:
    @patch("slack_sdk.WebClient")
    @patch("slack.handler._get_state_store")
    def test_sends_ephemeral_with_bot_token(self, mock_get_store, mock_wc_cls):
        mock_store = MagicMock()
        mock_config = MagicMock()
        mock_config.bot_token = "xoxb-test"
        mock_store.get_workspace_config.return_value = mock_config
        mock_get_store.return_value = mock_store

        mock_client = MagicMock()
        mock_wc_cls.return_value = mock_client

        _send_ephemeral_rejection(
            workspace_id="W1",
            channel_id="C1",
            user_id="U1",
            text="Rate limited",
        )
        mock_client.chat_postEphemeral.assert_called_once_with(
            channel="C1", user="U1", text="Rate limited"
        )

    @patch("slack_sdk.WebClient")
    @patch("slack.handler._get_state_store")
    def test_skips_when_no_config(self, mock_get_store, mock_wc_cls):
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = None
        mock_get_store.return_value = mock_store

        _send_ephemeral_rejection(
            workspace_id="W1",
            channel_id="C1",
            user_id="U1",
            text="Rate limited",
        )
        mock_wc_cls.assert_not_called()

    @patch("slack_sdk.WebClient")
    @patch("slack.handler._get_state_store")
    def test_handles_api_error_gracefully(self, mock_get_store, mock_wc_cls):
        mock_store = MagicMock()
        mock_config = MagicMock()
        mock_config.bot_token = "xoxb-test"
        mock_store.get_workspace_config.return_value = mock_config
        mock_get_store.return_value = mock_store

        mock_client = MagicMock()
        mock_client.chat_postEphemeral.side_effect = Exception("Slack API error")
        mock_wc_cls.return_value = mock_client

        # Should not raise
        _send_ephemeral_rejection(
            workspace_id="W1",
            channel_id="C1",
            user_id="U1",
            text="Rate limited",
        )
