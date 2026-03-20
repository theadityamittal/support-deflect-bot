"""Tests for the agent worker Lambda."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import agent.worker as _worker_module
import pytest
from agent.worker import _get_bot_token, lambda_handler


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
    @patch("agent.worker._release_user_lock")
    @patch("agent.worker._get_setup_state", return_value=None)
    @patch("agent.worker._get_bot_token")
    @patch("agent.worker._create_orchestrator")
    @patch("agent.worker.SlackClient")
    @patch("agent.worker.WebClient")
    def test_processes_sqs_message(
        self,
        mock_web_client_cls,
        mock_slack_client_cls,
        mock_create_orch,
        mock_get_token,
        mock_get_setup,
        mock_release,
    ):
        mock_get_token.return_value = "xoxb-fake"
        mock_slack_client = MagicMock()
        mock_slack_client_cls.return_value = mock_slack_client
        mock_orch = MagicMock()
        mock_orch.process_turn.return_value = "Hello volunteer!"
        mock_create_orch.return_value = mock_orch

        event = _sqs_event(_message_body())
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        mock_orch.process_turn.assert_called_once_with(user_message="hi")
        mock_slack_client.send_message.assert_called_once_with(
            channel="C1", text="Hello volunteer!"
        )
        mock_release.assert_called_once_with(workspace_id="W1", user_id="U1")

    @patch("agent.worker._release_user_lock")
    @patch("agent.worker._get_setup_state", return_value=None)
    @patch("agent.worker._get_bot_token")
    @patch("agent.worker._create_orchestrator")
    @patch("agent.worker.SlackClient")
    @patch("agent.worker.WebClient")
    def test_handles_orchestrator_error(
        self,
        mock_web_client_cls,
        mock_slack_client_cls,
        mock_create_orch,
        mock_get_token,
        mock_get_setup,
        mock_release,
    ):
        mock_get_token.return_value = "xoxb-fake"
        mock_slack_client = MagicMock()
        mock_slack_client_cls.return_value = mock_slack_client
        mock_orch = MagicMock()
        mock_orch.process_turn.side_effect = Exception("LLM timeout")
        mock_create_orch.return_value = mock_orch

        event = _sqs_event(_message_body())
        result = lambda_handler(event, None)

        assert result["statusCode"] == 500
        mock_slack_client.send_message.assert_not_called()
        mock_release.assert_called_once_with(workspace_id="W1", user_id="U1")

    @patch("agent.worker._get_bot_token")
    @patch("agent.worker._create_orchestrator")
    @patch("agent.worker.SlackClient")
    @patch("agent.worker.WebClient")
    def test_empty_records(
        self,
        mock_web_client_cls,
        mock_slack_client_cls,
        mock_create_orch,
        mock_get_token,
    ):
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


class TestWorkerSetupRouting:
    """Tests for SETUP record routing in the worker Lambda."""

    def _make_setup_state(self, *, admin_user_id: str = "UADMIN") -> MagicMock:
        setup_state = MagicMock()
        setup_state.admin_user_id = admin_user_id
        return setup_state

    @patch("agent.worker._release_user_lock")
    @patch("agent.worker._get_bot_token")
    @patch("agent.worker._create_orchestrator")
    @patch("agent.worker.SlackClient")
    @patch("agent.worker.WebClient")
    @patch("agent.worker._get_setup_state")
    @patch("agent.worker._call_process_setup_message")
    def test_setup_record_routes_admin_to_state_machine(
        self,
        mock_call_setup,
        mock_get_setup_state,
        mock_web_client_cls,
        mock_slack_client_cls,
        mock_create_orch,
        mock_get_token,
        mock_release,
    ):
        """When SETUP record exists and user is the admin, route to setup state machine."""
        mock_get_token.return_value = "xoxb-fake"
        mock_slack_client = MagicMock()
        mock_slack_client_cls.return_value = mock_slack_client

        setup_state = self._make_setup_state(admin_user_id="UADMIN")
        mock_get_setup_state.return_value = setup_state

        event = _sqs_event(_message_body(user_id="UADMIN", workspace_id="W1"))
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        mock_call_setup.assert_called_once()
        mock_create_orch.assert_not_called()
        mock_release.assert_called_once_with(workspace_id="W1", user_id="UADMIN")

    @patch("agent.worker._release_user_lock")
    @patch("agent.worker._get_bot_token")
    @patch("agent.worker._create_orchestrator")
    @patch("agent.worker.SlackClient")
    @patch("agent.worker.WebClient")
    @patch("agent.worker._get_setup_state")
    @patch("agent.worker._call_process_setup_message")
    def test_setup_record_rejects_non_admin(
        self,
        mock_call_setup,
        mock_get_setup_state,
        mock_web_client_cls,
        mock_slack_client_cls,
        mock_create_orch,
        mock_get_token,
        mock_release,
    ):
        """When SETUP record exists but user is NOT the admin, send ephemeral rejection."""
        mock_get_token.return_value = "xoxb-fake"
        mock_slack_client = MagicMock()
        mock_slack_client_cls.return_value = mock_slack_client

        setup_state = self._make_setup_state(admin_user_id="UADMIN")
        mock_get_setup_state.return_value = setup_state

        event = _sqs_event(_message_body(user_id="UOTHER", workspace_id="W1"))
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        mock_call_setup.assert_not_called()
        mock_create_orch.assert_not_called()
        mock_slack_client.send_ephemeral.assert_called_once()
        # Verify the ephemeral was sent to the right user/channel
        call_kwargs = mock_slack_client.send_ephemeral.call_args.kwargs
        assert call_kwargs["user"] == "UOTHER"
        mock_release.assert_called_once_with(workspace_id="W1", user_id="UOTHER")

    @patch("agent.worker._release_user_lock")
    @patch("agent.worker._get_bot_token")
    @patch("agent.worker._create_orchestrator")
    @patch("agent.worker.SlackClient")
    @patch("agent.worker.WebClient")
    @patch("agent.worker._get_setup_state")
    def test_no_setup_record_routes_to_orchestrator(
        self,
        mock_get_setup_state,
        mock_web_client_cls,
        mock_slack_client_cls,
        mock_create_orch,
        mock_get_token,
        mock_release,
    ):
        """When no SETUP record exists, normal orchestrator flow runs."""
        mock_get_token.return_value = "xoxb-fake"
        mock_slack_client = MagicMock()
        mock_slack_client_cls.return_value = mock_slack_client
        mock_orch = MagicMock()
        mock_orch.process_turn.return_value = "Hello!"
        mock_create_orch.return_value = mock_orch

        mock_get_setup_state.return_value = None  # No SETUP record

        event = _sqs_event(_message_body(user_id="U1", workspace_id="W1"))
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        mock_create_orch.assert_called_once()
        mock_orch.process_turn.assert_called_once_with(user_message="hi")
        mock_release.assert_called_once_with(workspace_id="W1", user_id="U1")

    @patch("agent.worker._release_user_lock")
    @patch("agent.worker._get_bot_token")
    @patch("agent.worker._create_orchestrator")
    @patch("agent.worker.SlackClient")
    @patch("agent.worker.WebClient")
    @patch("agent.worker._get_setup_state")
    @patch("agent.worker._call_process_setup_message")
    def test_interaction_action_routes_to_setup_when_applicable(
        self,
        mock_call_setup,
        mock_get_setup_state,
        mock_web_client_cls,
        mock_slack_client_cls,
        mock_create_orch,
        mock_get_token,
        mock_release,
    ):
        """Interaction event with action_id routes admin to setup state machine."""
        mock_get_token.return_value = "xoxb-fake"
        mock_slack_client = MagicMock()
        mock_slack_client_cls.return_value = mock_slack_client

        setup_state = self._make_setup_state(admin_user_id="UADMIN")
        mock_get_setup_state.return_value = setup_state

        body = _message_body(
            user_id="UADMIN",
            workspace_id="W1",
            event_type="interaction",
            metadata={
                "is_dm": True,
                "command": None,
                "thread_ts": None,
                "action_id": "teams_confirm",
                "action_value": None,
            },
        )
        event = _sqs_event(body)
        result = lambda_handler(event, None)

        assert result["statusCode"] == 200
        mock_call_setup.assert_called_once()
        # Verify action_id was passed through
        call_kwargs = mock_call_setup.call_args.kwargs
        assert call_kwargs.get("action_id") == "teams_confirm"
        mock_create_orch.assert_not_called()
        mock_release.assert_called_once_with(workspace_id="W1", user_id="UADMIN")
