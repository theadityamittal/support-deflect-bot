# tests/unit/test_interactions.py
"""Tests for Block Kit interaction handling pipeline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

import pytest

from slack.handler import _handle_interaction, lambda_handler
from slack.models import EventType, SQSMessage


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BLOCK_ACTION_PAYLOAD = {
    "type": "block_actions",
    "user": {"id": "U123"},
    "team": {"id": "T456"},
    "channel": {"id": "C789"},
    "message": {"ts": "123.456"},
    "actions": [{"action_id": "calendar_confirm", "value": "confirm_slot_1"}],
}


def _make_form_body(payload: dict) -> str:
    """URL-encode payload as Slack sends it for interactions."""
    return urlencode({"payload": json.dumps(payload)})


def _make_api_gw_interaction_event(body: str, headers: dict | None = None) -> dict:
    return {
        "path": "/slack/interactions",
        "httpMethod": "POST",
        "headers": headers or {},
        "body": body,
        "requestContext": {},
    }


# ---------------------------------------------------------------------------
# Unit tests for _handle_interaction
# ---------------------------------------------------------------------------


class TestParseBlockActionPayload:
    def test_parse_block_action_payload(self):
        """Valid block_actions payload is parsed without error."""
        body = _make_form_body(_BLOCK_ACTION_PAYLOAD)
        with patch("slack.handler._enqueue_to_sqs") as mock_enqueue:
            result = _handle_interaction(body)

        assert result["statusCode"] == 200
        mock_enqueue.assert_called_once()

    def test_missing_payload_field_returns_400(self):
        """Body without a 'payload' key returns 400."""
        result = _handle_interaction("no_payload=true")
        assert result["statusCode"] == 400
        assert "Missing payload" in json.loads(result["body"])["error"]

    def test_malformed_json_payload_returns_400(self):
        """URL-encoded payload that isn't valid JSON returns 400."""
        body = urlencode({"payload": "not-json{"})
        result = _handle_interaction(body)
        assert result["statusCode"] == 400
        assert "Invalid payload" in json.loads(result["body"])["error"]

    def test_unsupported_type_returns_400(self):
        """Non block_actions type returns 400."""
        payload = {**_BLOCK_ACTION_PAYLOAD, "type": "view_submission"}
        body = _make_form_body(payload)
        result = _handle_interaction(body)
        assert result["statusCode"] == 400
        assert "Unsupported type" in json.loads(result["body"])["error"]


class TestNormalizeInteractionToSQSMessage:
    def test_normalize_interaction_to_sqs_message(self):
        """Interaction payload produces an SQSMessage with action_id/action_value."""
        body = _make_form_body(_BLOCK_ACTION_PAYLOAD)
        captured: list[SQSMessage] = []

        with patch("slack.handler._enqueue_to_sqs", side_effect=captured.append):
            _handle_interaction(body)

        assert len(captured) == 1
        msg = captured[0]
        assert msg.event_type == EventType.INTERACTION
        assert msg.action_id == "calendar_confirm"
        assert msg.action_value == "confirm_slot_1"
        assert msg.user_id == "U123"
        assert msg.workspace_id == "T456"
        assert msg.channel_id == "C789"

    def test_sqs_message_to_dict_includes_action_fields(self):
        """to_dict serializes action_id and action_value into metadata."""
        msg = SQSMessage(
            version="1.0",
            event_id="evt-1",
            workspace_id="T1",
            user_id="U1",
            channel_id="C1",
            event_type=EventType.INTERACTION,
            text="",
            timestamp="1.0",
            action_id="calendar_confirm",
            action_value="slot_1",
        )
        data = msg.to_dict()
        assert data["metadata"]["action_id"] == "calendar_confirm"
        assert data["metadata"]["action_value"] == "slot_1"
        assert data["event_type"] == "interaction"

    def test_sqs_message_round_trip(self):
        """from_sqs_record deserializes action_id and action_value."""
        msg = SQSMessage(
            version="1.0",
            event_id="evt-2",
            workspace_id="T1",
            user_id="U1",
            channel_id="C1",
            event_type=EventType.INTERACTION,
            text="",
            timestamp="2.0",
            action_id="calendar_decline",
            action_value="no_thanks",
        )
        record = {"body": json.dumps(msg.to_dict())}
        restored = SQSMessage.from_sqs_record(record)
        assert restored.action_id == "calendar_decline"
        assert restored.action_value == "no_thanks"
        assert restored.event_type == EventType.INTERACTION


class TestInteractionEnqueuedToSQS:
    def test_interaction_enqueued_to_sqs(self):
        """Allowed interaction is enqueued exactly once."""
        body = _make_form_body(_BLOCK_ACTION_PAYLOAD)

        with patch("slack.handler._enqueue_to_sqs") as mock_enqueue:
            _handle_interaction(body)

        mock_enqueue.assert_called_once()
        enqueued: SQSMessage = mock_enqueue.call_args[0][0]
        assert enqueued.event_type == EventType.INTERACTION
        assert enqueued.action_id == "calendar_confirm"

    def test_interaction_no_actions_field_produces_empty_action(self):
        """Payload with no 'actions' still enqueues with empty action_id."""
        payload = {**_BLOCK_ACTION_PAYLOAD, "actions": []}
        body = _make_form_body(payload)

        with patch("slack.handler._enqueue_to_sqs") as mock_enqueue:
            _handle_interaction(body)

        mock_enqueue.assert_called_once()
        enqueued: SQSMessage = mock_enqueue.call_args[0][0]
        assert enqueued.action_id == ""


class TestInteractionReturns200:
    def test_interaction_returns_200(self):
        """Successful interaction always returns 200."""
        body = _make_form_body(_BLOCK_ACTION_PAYLOAD)

        with patch("slack.handler._enqueue_to_sqs"):
            result = _handle_interaction(body)

        assert result["statusCode"] == 200
        assert json.loads(result["body"]) == {"ok": True}


class TestInvalidPayloadReturns400:
    def test_invalid_payload_returns_400(self):
        """Empty body returns 400."""
        result = _handle_interaction("")
        assert result["statusCode"] == 400

    def test_empty_payload_value_returns_400(self):
        """URL-encoded body with empty payload value returns 400."""
        body = urlencode({"payload": ""})
        result = _handle_interaction(body)
        assert result["statusCode"] == 400


# ---------------------------------------------------------------------------
# Integration: full lambda_handler path for /slack/interactions
# ---------------------------------------------------------------------------


class TestLambdaHandlerInteraction:
    @patch("slack.handler._get_signing_secret")
    @patch("slack.handler.verify_slack_signature")
    @patch("slack.handler._enqueue_to_sqs")
    def test_full_pipeline_via_lambda_handler(
        self, mock_enqueue, mock_verify, mock_secret
    ):
        """lambda_handler routes /slack/interactions through the full pipeline."""
        mock_secret.return_value = "secret"

        body = _make_form_body(_BLOCK_ACTION_PAYLOAD)
        event = _make_api_gw_interaction_event(body)
        result = lambda_handler(event, {})

        assert result["statusCode"] == 200
        mock_enqueue.assert_called_once()


class TestEventIdIncludesActionId:
    def test_event_id_includes_action_id(self):
        """Event ID should include action_id for SQS FIFO dedup uniqueness."""
        body = _make_form_body(_BLOCK_ACTION_PAYLOAD)
        captured: list[SQSMessage] = []

        with patch("slack.handler._enqueue_to_sqs", side_effect=captured.append):
            _handle_interaction(body)

        assert len(captured) == 1
        msg = captured[0]
        assert msg.event_id == "interaction:T456:U123:123.456:calendar_confirm"

    def test_event_id_empty_action_id(self):
        """Event ID should have empty action_id segment when no actions."""
        payload = {**_BLOCK_ACTION_PAYLOAD, "actions": []}
        body = _make_form_body(payload)
        captured: list[SQSMessage] = []

        with patch("slack.handler._enqueue_to_sqs", side_effect=captured.append):
            _handle_interaction(body)

        assert len(captured) == 1
        assert captured[0].event_id == "interaction:T456:U123:123.456:"


class TestInteractionSkipsMiddleware:
    def test_interaction_does_not_call_middleware(self):
        """Interactions should skip the handler middleware chain entirely."""
        body = _make_form_body(_BLOCK_ACTION_PAYLOAD)

        with (
            patch("slack.handler._build_middleware_chain") as mock_chain_builder,
            patch("slack.handler._enqueue_to_sqs"),
        ):
            _handle_interaction(body)

        mock_chain_builder.assert_not_called()

    def test_interaction_enqueues_without_middleware(self):
        """Valid interaction should enqueue directly to SQS."""
        body = _make_form_body(_BLOCK_ACTION_PAYLOAD)

        with patch("slack.handler._enqueue_to_sqs") as mock_enqueue:
            result = _handle_interaction(body)

        assert result["statusCode"] == 200
        mock_enqueue.assert_called_once()


class TestActionValueExtraction:
    def test_button_value_extracted_from_value_field(self):
        """Button actions use 'value' field."""
        payload = {
            **_BLOCK_ACTION_PAYLOAD,
            "actions": [{"action_id": "teams_confirm", "value": "confirmed"}],
        }
        body = _make_form_body(payload)
        captured: list[SQSMessage] = []

        with patch("slack.handler._enqueue_to_sqs", side_effect=captured.append):
            _handle_interaction(body)

        assert captured[0].action_value == "confirmed"

    def test_dropdown_value_extracted_from_selected_option(self):
        """static_select actions use 'selected_option.value'."""
        payload = {
            **_BLOCK_ACTION_PAYLOAD,
            "actions": [
                {
                    "action_id": "channel_map_engineering",
                    "type": "static_select",
                    "selected_option": {
                        "value": "C_ENG",
                        "text": {"type": "plain_text", "text": "engineering"},
                    },
                }
            ],
        }
        body = _make_form_body(payload)
        captured: list[SQSMessage] = []

        with patch("slack.handler._enqueue_to_sqs", side_effect=captured.append):
            _handle_interaction(body)

        assert captured[0].action_value == "C_ENG"

    def test_no_value_or_selected_option_returns_empty(self):
        """Action with neither value nor selected_option returns empty string."""
        payload = {
            **_BLOCK_ACTION_PAYLOAD,
            "actions": [{"action_id": "some_action"}],
        }
        body = _make_form_body(payload)
        captured: list[SQSMessage] = []

        with patch("slack.handler._enqueue_to_sqs", side_effect=captured.append):
            _handle_interaction(body)

        assert captured[0].action_value == ""
