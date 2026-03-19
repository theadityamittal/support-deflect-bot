"""Tests for the agent worker Lambda."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import agent.worker as _worker_module
import pytest
from agent.worker import _get_bot_token, _send_slack_message, lambda_handler


@pytest.fixture(autouse=True)
def _reset_secret_cache():
    """Clear the module-level secret cache between tests."""
    _worker_module._cached_secrets = None
    yield
    _worker_module._cached_secrets = None


def _sqs_event(body: dict) -> dict:
    return {"Records": [{"body": json.dumps(body)}]}


def _message_body(**overrides) -> dict:
    base = {
        "version": "1.0",
        "event_id": "Ev001",
        "workspace_id": "W1",
        "user_id": "U1",
        "channel_id": "C1",
        "event_type": "message",
        "text": "hi",
        "timestamp": "2026-03-19T10:00:00Z",
        "metadata": {"is_dm": True, "command": None, "thread_ts": None},
    }
    base.update(overrides)
    return base


class TestLambdaHandler:
    @patch("agent.worker._send_slack_message")
    @patch("agent.worker._get_bot_token")
    @patch("agent.worker._create_orchestrator")
    def test_processes_sqs_message(self, mock_create_orch, mock_get_token, mock_send):
        mock_get_token.return_value = "xoxb-fake"
        mock_orch = MagicMock()
        mock_orch.process_turn.return_value = "Hello volunteer!"
        mock_create_orch.return_value = mock_orch

        event = _sqs_event(_message_body())
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        mock_orch.process_turn.assert_called_once_with(user_message="hi")
        mock_send.assert_called_once_with(
            bot_token="xoxb-fake", channel_id="C1", text="Hello volunteer!"
        )

    @patch("agent.worker._send_slack_message")
    @patch("agent.worker._get_bot_token")
    @patch("agent.worker._create_orchestrator")
    def test_handles_orchestrator_error(
        self, mock_create_orch, mock_get_token, mock_send
    ):
        mock_get_token.return_value = "xoxb-fake"
        mock_orch = MagicMock()
        mock_orch.process_turn.side_effect = Exception("LLM timeout")
        mock_create_orch.return_value = mock_orch

        event = _sqs_event(_message_body())
        result = lambda_handler(event, None)

        assert result["statusCode"] == 500
        mock_send.assert_not_called()

    @patch("agent.worker._send_slack_message")
    @patch("agent.worker._get_bot_token")
    @patch("agent.worker._create_orchestrator")
    def test_empty_records(self, mock_create_orch, mock_get_token, mock_send):
        result = lambda_handler({"Records": []}, None)
        assert result["statusCode"] == 200
        mock_create_orch.assert_not_called()


class TestGetBotToken:
    @patch("agent.worker.boto3")
    def test_returns_token_from_secrets_manager(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"bot_token": "xoxb-real-token"})
        }

        with patch.dict("os.environ", {"APP_SECRETS_ARN": "arn:aws:sm:test"}):
            token = _get_bot_token("W1")

        assert token == "xoxb-real-token"

    @patch("agent.worker.boto3")
    def test_falls_back_to_dynamo_when_placeholder(self, mock_boto3):
        mock_sm_client = MagicMock()
        mock_boto3.client.return_value = mock_sm_client
        mock_sm_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"bot_token": "placeholder"})
        }
        mock_table = MagicMock()
        mock_boto3.resource.return_value.Table.return_value = mock_table

        mock_config = MagicMock()
        mock_config.bot_token = "xoxb-dynamo-token"

        with (
            patch.dict("os.environ", {"APP_SECRETS_ARN": "arn:aws:sm:test"}),
            patch("state.dynamo.DynamoStateStore") as mock_store_cls,
        ):
            mock_store_cls.return_value.get_workspace_config.return_value = mock_config
            token = _get_bot_token("W1")

        assert token == "xoxb-dynamo-token"

    @patch("agent.worker.boto3")
    def test_raises_when_no_token_found(self, mock_boto3):
        mock_table = MagicMock()
        mock_boto3.resource.return_value.Table.return_value = mock_table

        with (
            patch.dict("os.environ", {}, clear=False),
            patch("state.dynamo.DynamoStateStore") as mock_store_cls,
        ):
            mock_store_cls.return_value.get_workspace_config.return_value = None
            with pytest.raises(ValueError, match="No bot token"):
                _get_bot_token("W_MISSING")


class TestSendSlackMessage:
    @patch("slack_sdk.WebClient")
    def test_sends_message(self, mock_web_client_cls):
        mock_client = MagicMock()
        mock_web_client_cls.return_value = mock_client

        _send_slack_message(bot_token="xoxb-test", channel_id="C1", text="hello")

        mock_web_client_cls.assert_called_once_with(token="xoxb-test")
        mock_client.chat_postMessage.assert_called_once_with(channel="C1", text="hello")
