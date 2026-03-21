# tests/unit/test_slack_handler.py
"""Tests for the Slack Handler Lambda entry point."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from slack.handler import (
    _build_middleware_chain,
    _check_setup_gating,
    _send_ephemeral_rejection,
    lambda_handler,
)


@pytest.fixture(autouse=True)
def _disable_kill_switch():
    """Prevent unit tests from hitting real DynamoDB for kill switch checks."""
    with patch("admin.kill_switch_check.is_kill_switch_active", return_value=False):
        yield


@pytest.fixture(autouse=True)
def _mock_state_store():
    """Prevent unit tests from creating real boto3 DynamoDB resources."""
    with patch("slack.handler._get_state_store", return_value=MagicMock()):
        yield


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
    @patch("slack.handler._check_setup_gating", return_value=None)
    def test_event_passes_middleware_and_enqueues(
        self, mock_gating, mock_chain_builder, mock_enqueue, mock_verify, mock_secret
    ):
        mock_secret.return_value = "secret"
        mock_chain = MagicMock()
        mock_chain.run.return_value = MagicMock(allowed=True)
        mock_chain_builder.return_value = mock_chain

        event = _make_api_gw_event(path="/slack/events", body=_EVENT_BODY)
        result = lambda_handler(event, {})
        assert result["statusCode"] == 200
        mock_gating.assert_called_once()
        mock_enqueue.assert_called_once()

    @patch("slack.handler._get_signing_secret")
    @patch("slack.handler.verify_slack_signature")
    @patch("slack.handler._send_ephemeral_rejection")
    @patch("slack.handler._build_middleware_chain")
    @patch("slack.handler._check_setup_gating", return_value=None)
    def test_reject_sends_ephemeral(
        self, mock_gating, mock_chain_builder, mock_ephemeral, mock_verify, mock_secret
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
        mock_gating.assert_called_once()
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
    @patch("slack.handler._check_setup_gating", return_value=None)
    def test_drop_does_not_send_ephemeral(
        self, mock_gating, mock_chain_builder, mock_ephemeral, mock_verify, mock_secret
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
        mock_gating.assert_called_once()
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
                "command": "/sherpa-help",
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
    @patch("slack.handler._enqueue_to_sqs")
    @patch("slack.handler._build_middleware_chain")
    def test_interaction_path_returns_200(
        self, mock_chain_builder, mock_enqueue, mock_verify, mock_secret
    ):
        import json as _json
        from urllib.parse import urlencode

        mock_secret.return_value = "secret"
        mock_chain = MagicMock()
        mock_chain.run.return_value = MagicMock(allowed=True)
        mock_chain_builder.return_value = mock_chain

        payload = {
            "type": "block_actions",
            "user": {"id": "U1"},
            "team": {"id": "T1"},
            "channel": {"id": "C1"},
            "message": {"ts": "1.0"},
            "actions": [{"action_id": "btn", "value": "ok"}],
        }
        body = urlencode({"payload": _json.dumps(payload)})
        event = {
            "path": "/slack/interactions",
            "httpMethod": "POST",
            "headers": {},
            "body": body,
            "requestContext": {},
        }
        result = lambda_handler(event, {})
        assert result["statusCode"] == 200
        mock_chain.run.assert_called_once()
        mock_enqueue.assert_called_once()


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
    @patch("slack.handler.SlackClient")
    @patch("slack.handler.WebClient")
    @patch("slack.handler._get_state_store")
    def test_sends_ephemeral_with_bot_token(
        self, mock_get_store, mock_wc_cls, mock_sc_cls
    ):
        mock_store = MagicMock()
        mock_config = MagicMock()
        mock_config.bot_token = "xoxb-test"
        mock_store.get_workspace_config.return_value = mock_config
        mock_get_store.return_value = mock_store

        mock_slack_client = MagicMock()
        mock_sc_cls.return_value = mock_slack_client

        _send_ephemeral_rejection(
            workspace_id="W1",
            channel_id="C1",
            user_id="U1",
            text="Rate limited",
        )
        mock_wc_cls.assert_called_once_with(token="xoxb-test")
        mock_sc_cls.assert_called_once_with(web_client=mock_wc_cls.return_value)
        mock_slack_client.send_ephemeral.assert_called_once_with(
            channel="C1", user="U1", text="Rate limited"
        )

    @patch("slack.handler.SlackClient")
    @patch("slack.handler.WebClient")
    @patch("slack.handler._get_state_store")
    def test_skips_when_no_config(self, mock_get_store, mock_wc_cls, mock_sc_cls):
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = None
        mock_get_store.return_value = mock_store

        _send_ephemeral_rejection(
            workspace_id="W1",
            channel_id="C1",
            user_id="U1",
            text="Rate limited",
        )
        mock_sc_cls.assert_not_called()

    @patch("slack.handler.SlackClient")
    @patch("slack.handler.WebClient")
    @patch("slack.handler._get_state_store")
    def test_handles_api_error_gracefully(
        self, mock_get_store, mock_wc_cls, mock_sc_cls
    ):
        mock_store = MagicMock()
        mock_config = MagicMock()
        mock_config.bot_token = "xoxb-test"
        mock_store.get_workspace_config.return_value = mock_config
        mock_get_store.return_value = mock_store

        mock_slack_client = MagicMock()
        mock_slack_client.send_ephemeral.side_effect = Exception("Slack API error")
        mock_sc_cls.return_value = mock_slack_client

        # Should not raise
        _send_ephemeral_rejection(
            workspace_id="W1",
            channel_id="C1",
            user_id="U1",
            text="Rate limited",
        )
        mock_wc_cls.assert_called_once_with(token="xoxb-test")
        mock_sc_cls.assert_called_once_with(web_client=mock_wc_cls.return_value)


# ---------------------------------------------------------------------------
# Helper to build a minimal SlackEvent mock for gating tests
# ---------------------------------------------------------------------------
def _make_slack_event(
    event_type: str = "message",
    user_id: str = "U123",
    workspace_id: str = "W456",
    channel_id: str = "C789",
) -> MagicMock:
    from slack.models import EventType

    mock_event = MagicMock()
    mock_event.event_type = EventType(event_type)
    mock_event.user_id = user_id
    mock_event.workspace_id = workspace_id
    mock_event.channel_id = channel_id
    return mock_event


class TestSetupGating:
    @patch("slack.handler._send_ephemeral_rejection")
    @patch("slack.handler._get_state_store")
    def test_setup_incomplete_non_admin_gets_ephemeral(
        self, mock_get_store, mock_ephemeral
    ):
        """Non-admin events during setup are blocked with an ephemeral message."""
        mock_store = MagicMock()
        mock_config = MagicMock()
        mock_config.setup_complete = False
        mock_config.admin_user_id = "UADMIN"
        mock_store.get_workspace_config.return_value = mock_config
        mock_get_store.return_value = mock_store

        slack_event = _make_slack_event(user_id="U_OTHER")
        result = _check_setup_gating(slack_event)

        assert result is not None
        assert result["statusCode"] == 200
        mock_ephemeral.assert_called_once_with(
            workspace_id="W456",
            channel_id="C789",
            user_id="U_OTHER",
            text="We're still setting up. Please check back soon!",
        )

    @patch("slack.handler._send_ephemeral_rejection")
    @patch("slack.handler._get_state_store")
    def test_setup_incomplete_admin_passes_through(
        self, mock_get_store, mock_ephemeral
    ):
        """Admin user events are allowed through even when setup is incomplete."""
        mock_store = MagicMock()
        mock_config = MagicMock()
        mock_config.setup_complete = False
        mock_config.admin_user_id = "UADMIN"
        mock_store.get_workspace_config.return_value = mock_config
        mock_get_store.return_value = mock_store

        slack_event = _make_slack_event(user_id="UADMIN")
        result = _check_setup_gating(slack_event)

        assert result is None
        mock_ephemeral.assert_not_called()

    @patch("slack.handler._send_ephemeral_rejection")
    @patch("slack.handler._get_state_store")
    def test_setup_complete_all_users_pass_through(
        self, mock_get_store, mock_ephemeral
    ):
        """All users pass through when setup is complete."""
        mock_store = MagicMock()
        mock_config = MagicMock()
        mock_config.setup_complete = True
        mock_store.get_workspace_config.return_value = mock_config
        mock_get_store.return_value = mock_store

        slack_event = _make_slack_event(user_id="U_ANYONE")
        result = _check_setup_gating(slack_event)

        assert result is None
        mock_ephemeral.assert_not_called()

    @patch("slack.handler._send_setup_pending_dm")
    @patch("slack.handler._get_state_store")
    def test_team_join_during_setup_creates_pending_plan(self, mock_get_store, mock_dm):
        """team_join during setup creates a PENDING_SETUP OnboardingPlan."""
        from state.models import PlanStatus

        mock_store = MagicMock()
        mock_config = MagicMock()
        mock_config.setup_complete = False
        mock_config.admin_user_id = "UADMIN"
        mock_store.get_workspace_config.return_value = mock_config
        mock_get_store.return_value = mock_store

        slack_event = _make_slack_event(event_type="team_join", user_id="UNEW")
        result = _check_setup_gating(slack_event)

        assert result is not None
        assert result["statusCode"] == 200
        mock_store.save_plan.assert_called_once()
        saved_plan = mock_store.save_plan.call_args[0][0]
        assert saved_plan.status == PlanStatus.PENDING_SETUP
        assert saved_plan.user_id == "UNEW"
        assert saved_plan.workspace_id == "W456"

    @patch("slack.handler._send_setup_pending_dm")
    @patch("slack.handler._get_state_store")
    def test_team_join_during_setup_sends_brief_dm(self, mock_get_store, mock_dm):
        """team_join during setup sends a brief welcome DM."""
        mock_store = MagicMock()
        mock_config = MagicMock()
        mock_config.setup_complete = False
        mock_config.admin_user_id = "UADMIN"
        mock_store.get_workspace_config.return_value = mock_config
        mock_get_store.return_value = mock_store

        slack_event = _make_slack_event(event_type="team_join", user_id="UNEW")
        _check_setup_gating(slack_event)

        mock_dm.assert_called_once_with(
            workspace_id="W456",
            user_id="UNEW",
        )


class TestGetSigningSecret:
    @patch.dict(
        "os.environ", {"APP_SECRETS_ARN": "", "SLACK_SIGNING_SECRET": "env-secret"}
    )
    def test_returns_from_env_when_no_arn(self):
        from slack.handler import _get_signing_secret

        assert _get_signing_secret() == "env-secret"

    @patch("slack.handler.boto3")
    @patch.dict("os.environ", {"APP_SECRETS_ARN": "arn:aws:sm:test"})
    def test_returns_from_secrets_manager(self, mock_boto3):
        from slack.handler import _get_signing_secret

        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"signing_secret": "sm-secret"})
        }
        assert _get_signing_secret() == "sm-secret"

    @patch("slack.handler.boto3")
    @patch.dict("os.environ", {"APP_SECRETS_ARN": "arn:aws:sm:test"})
    def test_returns_raw_string_on_json_error(self, mock_boto3):
        from slack.handler import _get_signing_secret

        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.get_secret_value.return_value = {"SecretString": "plain-secret"}
        assert _get_signing_secret() == "plain-secret"


class TestEnqueueToSqs:
    @patch("slack.handler.boto3")
    @patch.dict("os.environ", {"SQS_QUEUE_URL": "https://sqs.test/queue.fifo"})
    def test_sends_message_to_sqs(self, mock_boto3):
        from slack.handler import _enqueue_to_sqs
        from slack.models import EventType, SQSMessage

        mock_sqs = MagicMock()
        mock_boto3.client.return_value = mock_sqs

        msg = SQSMessage(
            version="1.0",
            event_id="Ev1",
            workspace_id="W1",
            user_id="U1",
            channel_id="C1",
            event_type=EventType.MESSAGE,
            text="hi",
            timestamp="2026-01-01T00:00:00Z",
        )
        _enqueue_to_sqs(msg)
        mock_sqs.send_message.assert_called_once()

    @patch("slack.handler.boto3")
    @patch.dict("os.environ", {"SQS_QUEUE_URL": ""})
    def test_skips_when_no_queue_url(self, mock_boto3):
        from slack.handler import _enqueue_to_sqs
        from slack.models import EventType, SQSMessage

        msg = SQSMessage(
            version="1.0",
            event_id="Ev1",
            workspace_id="W1",
            user_id="U1",
            channel_id="C1",
            event_type=EventType.MESSAGE,
            text="hi",
            timestamp="2026-01-01T00:00:00Z",
        )
        _enqueue_to_sqs(msg)
        mock_boto3.client.assert_not_called()


class TestSendSetupPendingDm:
    @patch("slack.handler.SlackClient")
    @patch("slack.handler.WebClient")
    @patch("slack.handler._get_bot_token_for_workspace", return_value="xoxb-test")
    def test_sends_dm_on_success(self, mock_get_token, mock_wc_cls, mock_sc_cls):
        from slack.handler import _send_setup_pending_dm

        mock_slack = MagicMock()
        mock_sc_cls.return_value = mock_slack

        _send_setup_pending_dm(workspace_id="W1", user_id="U1")

        mock_slack.send_message.assert_called_once()
        call_kwargs = mock_slack.send_message.call_args.kwargs
        assert call_kwargs["channel"] == "U1"
        assert "setting up" in call_kwargs["text"].lower()

    @patch(
        "slack.handler._get_bot_token_for_workspace", side_effect=ValueError("no token")
    )
    def test_skips_when_no_token(self, mock_get_token):
        from slack.handler import _send_setup_pending_dm

        # Should not raise
        _send_setup_pending_dm(workspace_id="W1", user_id="U1")


class TestHandlerKillSwitch:
    @patch("slack.handler._get_signing_secret", return_value="test-secret")
    @patch("slack.handler.verify_slack_signature")
    @patch("admin.kill_switch_check.is_kill_switch_active", return_value=True)
    @patch("slack.handler._get_state_store")
    def test_returns_200_when_kill_switch_active(
        self, mock_store, mock_kill, mock_verify, mock_secret
    ):
        """Handler returns 200 and skips enqueue when kill switch is on."""
        event = {
            "path": "/slack/events",
            "headers": {
                "X-Slack-Request-Timestamp": "123",
                "X-Slack-Signature": "v0=abc",
            },
            "body": json.dumps(
                {
                    "type": "event_callback",
                    "event": {
                        "type": "message",
                        "user": "U1",
                        "text": "hi",
                        "channel": "C1",
                        "ts": "1",
                    },
                    "event_id": "Ev1",
                    "team_id": "W1",
                }
            ),
        }
        result = lambda_handler(event, None)
        assert result["statusCode"] == 200
