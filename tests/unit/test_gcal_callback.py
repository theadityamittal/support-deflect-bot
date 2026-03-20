"""Tests for Google OAuth callback Lambda."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from gcal.callback import lambda_handler


def _make_event(
    code: str = "auth_code_123",
    state: str = "W123",
    error: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if code:
        params["code"] = code
    if state:
        params["state"] = state
    if error:
        params["error"] = error
    return {
        "queryStringParameters": params,
        "headers": {},
        "requestContext": {},
    }


_FAKE_TOKENS: dict[str, Any] = {
    "access_token": "ya29.access",
    "refresh_token": "1//refresh",
    "expires_in": 3600,
    "token_type": "Bearer",
}

_FAKE_CONFIG = MagicMock()
_FAKE_CONFIG.team_name = "Acme"
_FAKE_CONFIG.bot_user_id = "B001"
_FAKE_CONFIG.bot_token = None
_FAKE_CONFIG.admin_user_id = "U999"
_FAKE_CONFIG.setup_complete = False
_FAKE_CONFIG.website_url = "https://acme.com"
_FAKE_CONFIG.teams = ()
_FAKE_CONFIG.channel_mapping = {}
_FAKE_CONFIG.calendar_enabled = False


class TestGoogleOAuthCallback:
    def _patch_all(
        self,
        *,
        tokens: dict[str, Any] | None = None,
        existing_secrets: dict[str, Any] | None = None,
        config: Any = _FAKE_CONFIG,
        bot_token: str = "xoxb-bot",
    ):
        """Return a dict of patches for the happy-path scenario."""
        return {
            "_exchange_code": patch(
                "gcal.callback._exchange_code",
                return_value=tokens if tokens is not None else _FAKE_TOKENS,
            ),
            "_store_tokens": patch("gcal.callback._store_tokens"),
            "_set_calendar_enabled": patch("gcal.callback._set_calendar_enabled"),
            "_notify_admin": patch("gcal.callback._notify_admin"),
            "_enqueue_continuation": patch("gcal.callback._enqueue_continuation"),
        }

    # ------------------------------------------------------------------
    # Happy-path tests using internal helper patches
    # ------------------------------------------------------------------

    def test_exchanges_code_for_tokens(self):
        patches = self._patch_all()
        with (
            patches["_exchange_code"] as mock_exchange,
            patches["_store_tokens"],
            patches["_set_calendar_enabled"],
            patches["_notify_admin"],
            patches["_enqueue_continuation"],
        ):
            result = lambda_handler(_make_event(), {})

        assert result["statusCode"] == 200
        mock_exchange.assert_called_once_with("auth_code_123")

    def test_stores_encrypted_refresh_token_in_secrets(self):
        patches = self._patch_all()
        with (
            patches["_exchange_code"],
            patches["_store_tokens"] as mock_store,
            patches["_set_calendar_enabled"],
            patches["_notify_admin"],
            patches["_enqueue_continuation"],
        ):
            lambda_handler(_make_event(), {})

        mock_store.assert_called_once_with(workspace_id="W123", tokens=_FAKE_TOKENS)

    def test_sets_calendar_enabled_true(self):
        patches = self._patch_all()
        with (
            patches["_exchange_code"],
            patches["_store_tokens"],
            patches["_set_calendar_enabled"] as mock_cal,
            patches["_notify_admin"],
            patches["_enqueue_continuation"],
        ):
            lambda_handler(_make_event(), {})

        mock_cal.assert_called_once_with(workspace_id="W123")

    def test_sends_dm_to_admin(self):
        patches = self._patch_all()
        with (
            patches["_exchange_code"],
            patches["_store_tokens"],
            patches["_set_calendar_enabled"],
            patches["_notify_admin"] as mock_dm,
            patches["_enqueue_continuation"],
        ):
            lambda_handler(_make_event(), {})

        mock_dm.assert_called_once_with(workspace_id="W123")

    def test_enqueues_sqs_continuation(self):
        patches = self._patch_all()
        with (
            patches["_exchange_code"],
            patches["_store_tokens"],
            patches["_set_calendar_enabled"],
            patches["_notify_admin"],
            patches["_enqueue_continuation"] as mock_sqs,
        ):
            lambda_handler(_make_event(), {})

        mock_sqs.assert_called_once_with(workspace_id="W123")

    def test_returns_html_success(self):
        patches = self._patch_all()
        with (
            patches["_exchange_code"],
            patches["_store_tokens"],
            patches["_set_calendar_enabled"],
            patches["_notify_admin"],
            patches["_enqueue_continuation"],
        ):
            result = lambda_handler(_make_event(), {})

        assert result["statusCode"] == 200
        assert result["headers"]["Content-Type"] == "text/html"
        assert "google calendar connected" in result["body"].lower()

    # ------------------------------------------------------------------
    # Error / edge-case tests
    # ------------------------------------------------------------------

    def test_handles_error_param(self):
        result = lambda_handler(_make_event(code="", error="access_denied"), {})
        assert result["statusCode"] == 200
        assert (
            "cancel" in result["body"].lower() or "cancelled" in result["body"].lower()
        )

    def test_handles_missing_code(self):
        event = {
            "queryStringParameters": {"state": "W123"},
            "headers": {},
            "requestContext": {},
        }
        result = lambda_handler(event, {})
        assert result["statusCode"] == 400
        assert "missing" in result["body"].lower() or "code" in result["body"].lower()

    # ------------------------------------------------------------------
    # Unit tests for internal helpers (_store_tokens, _set_calendar_enabled,
    # _notify_admin, _enqueue_continuation) with fine-grained mocks
    # ------------------------------------------------------------------

    def test_store_tokens_merges_existing_secrets(self):
        """_store_tokens merges new tokens with any previously stored secrets."""
        from gcal.callback import _store_tokens

        mock_encryptor_instance = MagicMock()
        mock_encryptor_cls = MagicMock(return_value=mock_encryptor_instance)
        mock_store = MagicMock()
        mock_store.get_workspace_secrets.return_value = {"bot_token": "xoxb-existing"}

        with (
            patch("gcal.callback.FieldEncryptor", mock_encryptor_cls),
            patch("gcal.callback._get_store", return_value=mock_store),
            patch.dict(
                "os.environ", {"KMS_KEY_ID": "arn:aws:kms:us-east-1:123:key/abc"}
            ),
        ):
            _store_tokens(workspace_id="W123", tokens=_FAKE_TOKENS)

        saved_blob = mock_store.save_workspace_secrets.call_args.kwargs["secrets_blob"]
        assert saved_blob["bot_token"] == "xoxb-existing"
        assert saved_blob["google_refresh_token"] == "1//refresh"
        assert saved_blob["google_access_token"] == "ya29.access"
        assert "access_token_expiry" in saved_blob

    def test_set_calendar_enabled_updates_config(self):
        """_set_calendar_enabled calls save_workspace_config with calendar_enabled=True."""
        from gcal.callback import _set_calendar_enabled

        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = _FAKE_CONFIG

        with patch("gcal.callback._get_store", return_value=mock_store):
            _set_calendar_enabled(workspace_id="W123")

        call_kwargs = mock_store.save_workspace_config.call_args.kwargs
        assert call_kwargs["calendar_enabled"] is True
        assert call_kwargs["workspace_id"] == "W123"

    def test_notify_admin_sends_correct_message(self):
        """_notify_admin sends DM with confirmation text to admin_user_id."""
        from gcal.callback import _notify_admin

        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = _FAKE_CONFIG
        mock_store.get_bot_token.return_value = "xoxb-bot"
        mock_slack_client_instance = MagicMock()
        mock_slack_client_cls = MagicMock(return_value=mock_slack_client_instance)

        with (
            patch("gcal.callback._get_store", return_value=mock_store),
            patch("gcal.callback.FieldEncryptor"),
            patch("gcal.callback.SlackClient", mock_slack_client_cls),
            patch("gcal.callback.WebClient"),
            patch.dict(
                "os.environ", {"KMS_KEY_ID": "arn:aws:kms:us-east-1:123:key/abc"}
            ),
        ):
            _notify_admin(workspace_id="W123")

        mock_slack_client_instance.send_message.assert_called_once()
        call_kwargs = mock_slack_client_instance.send_message.call_args.kwargs
        assert call_kwargs["channel"] == "U999"
        assert "google calendar connected" in call_kwargs["text"].lower()

    def test_enqueue_continuation_uses_correct_sqs_params(self):
        """_enqueue_continuation sets MessageGroupId and MessageDeduplicationId correctly."""
        from gcal.callback import _enqueue_continuation

        mock_sqs = MagicMock()

        with (
            patch("gcal.callback.boto3") as mock_boto3,
            patch.dict(
                "os.environ",
                {"SQS_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/test.fifo"},
            ),
        ):
            mock_boto3.client.return_value = mock_sqs
            _enqueue_continuation(workspace_id="W123")

        mock_sqs.send_message.assert_called_once()
        call_kwargs = mock_sqs.send_message.call_args.kwargs
        assert call_kwargs["MessageGroupId"] == "W123"
        assert call_kwargs["MessageDeduplicationId"].startswith("gcal-oauth-W123-")
        body = json.loads(call_kwargs["MessageBody"])
        assert body["workspace_id"] == "W123"
        assert body["type"] == "gcal_oauth_complete"
