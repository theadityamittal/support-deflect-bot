"""Tests for Slack OAuth callback Lambda."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from slack.oauth import _save_workspace_config, lambda_handler


def _make_api_gw_event(code: str = "test_code", error: str | None = None) -> dict:
    params = {"code": code} if code else {}
    if error:
        params["error"] = error
    return {
        "queryStringParameters": params,
        "headers": {},
        "requestContext": {},
    }


class TestSlackOAuthLambda:
    @patch("slack.oauth._exchange_code_for_token")
    @patch("slack.oauth._save_workspace_config")
    def test_successful_oauth(self, mock_save, mock_exchange):
        mock_exchange.return_value = {
            "ok": True,
            "team": {"id": "W456", "name": "Test Org"},
            "access_token": "xoxb-test-token",
            "bot_user_id": "B001",
        }
        result = lambda_handler(_make_api_gw_event(code="valid_code"), {})
        assert result["statusCode"] == 200
        assert (
            "success" in result["body"].lower() or "installed" in result["body"].lower()
        )
        mock_save.assert_called_once()

    @patch("slack.oauth._exchange_code_for_token")
    def test_oauth_error_from_slack(self, mock_exchange):
        mock_exchange.return_value = {"ok": False, "error": "invalid_code"}
        result = lambda_handler(_make_api_gw_event(code="bad_code"), {})
        assert result["statusCode"] == 400

    def test_user_denied_access(self):
        result = lambda_handler(_make_api_gw_event(code="", error="access_denied"), {})
        assert result["statusCode"] == 200
        assert "denied" in result["body"].lower() or "cancel" in result["body"].lower()

    def test_missing_code_parameter(self):
        event = {"queryStringParameters": {}, "headers": {}, "requestContext": {}}
        result = lambda_handler(event, {})
        assert result["statusCode"] == 400


class TestSlackOAuthEnhanced:
    """Tests for enhanced _save_workspace_config behaviour."""

    _TOKEN_RESPONSE = {
        "ok": True,
        "team": {"id": "T123", "name": "Acme Corp"},
        "access_token": "xoxb-real-bot-token",
        "bot_user_id": "B999",
        "authed_user": {"id": "U_ADMIN_001"},
    }

    def _make_mocks(self):
        mock_table = MagicMock()
        mock_store = MagicMock()
        mock_encryptor = MagicMock()
        mock_slack_client = MagicMock()
        return mock_table, mock_store, mock_encryptor, mock_slack_client

    @patch("slack.oauth.WebClient")
    @patch("slack.oauth.FieldEncryptor")
    @patch("slack.oauth.DynamoStateStore")
    @patch("slack.oauth.boto3")
    @patch.dict(
        "os.environ",
        {"DYNAMODB_TABLE_NAME": "test-table", "KMS_KEY_ID": "alias/test-key"},
    )
    def test_stores_admin_user_id_from_authed_user(
        self, mock_boto3, mock_store_cls, mock_encryptor_cls, mock_webclient_cls
    ):
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_encryptor_cls.return_value = MagicMock()
        mock_webclient_cls.return_value = MagicMock()

        _save_workspace_config(self._TOKEN_RESPONSE)

        mock_store.save_workspace_config.assert_called_once()
        call_kwargs = mock_store.save_workspace_config.call_args.kwargs
        assert call_kwargs["admin_user_id"] == "U_ADMIN_001"

    @patch("slack.oauth.WebClient")
    @patch("slack.oauth.FieldEncryptor")
    @patch("slack.oauth.DynamoStateStore")
    @patch("slack.oauth.boto3")
    @patch.dict(
        "os.environ",
        {"DYNAMODB_TABLE_NAME": "test-table", "KMS_KEY_ID": "alias/test-key"},
    )
    def test_sets_setup_complete_false(
        self, mock_boto3, mock_store_cls, mock_encryptor_cls, mock_webclient_cls
    ):
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_encryptor_cls.return_value = MagicMock()
        mock_webclient_cls.return_value = MagicMock()

        _save_workspace_config(self._TOKEN_RESPONSE)

        call_kwargs = mock_store.save_workspace_config.call_args.kwargs
        assert call_kwargs["setup_complete"] is False
        assert call_kwargs.get("bot_token") is None

    @patch("slack.oauth.WebClient")
    @patch("slack.oauth.FieldEncryptor")
    @patch("slack.oauth.DynamoStateStore")
    @patch("slack.oauth.boto3")
    @patch.dict(
        "os.environ",
        {"DYNAMODB_TABLE_NAME": "test-table", "KMS_KEY_ID": "alias/test-key"},
    )
    def test_encrypts_bot_token_in_secrets(
        self, mock_boto3, mock_store_cls, mock_encryptor_cls, mock_webclient_cls
    ):
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_encryptor = MagicMock()
        mock_encryptor_cls.return_value = mock_encryptor
        mock_webclient_cls.return_value = MagicMock()

        _save_workspace_config(self._TOKEN_RESPONSE)

        mock_store.save_workspace_secrets.assert_called_once_with(
            workspace_id="T123",
            secrets_blob={"bot_token": "xoxb-real-bot-token"},
            encryptor=mock_encryptor,
        )

    @patch("slack.oauth.WebClient")
    @patch("slack.oauth.FieldEncryptor")
    @patch("slack.oauth.DynamoStateStore")
    @patch("slack.oauth.boto3")
    @patch.dict(
        "os.environ",
        {"DYNAMODB_TABLE_NAME": "test-table", "KMS_KEY_ID": "alias/test-key"},
    )
    def test_creates_setup_record_after_token_storage(
        self, mock_boto3, mock_store_cls, mock_encryptor_cls, mock_webclient_cls
    ):
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_encryptor_cls.return_value = MagicMock()
        mock_webclient_cls.return_value = MagicMock()

        _save_workspace_config(self._TOKEN_RESPONSE)

        mock_store.save_setup_state.assert_called_once()
        setup_state_arg = mock_store.save_setup_state.call_args.kwargs["setup_state"]
        assert setup_state_arg.workspace_id == "T123"
        assert setup_state_arg.step == "welcome"
        assert setup_state_arg.admin_user_id == "U_ADMIN_001"

    @patch("slack.oauth.WebClient")
    @patch("slack.oauth.FieldEncryptor")
    @patch("slack.oauth.DynamoStateStore")
    @patch("slack.oauth.boto3")
    @patch.dict(
        "os.environ",
        {"DYNAMODB_TABLE_NAME": "test-table", "KMS_KEY_ID": "alias/test-key"},
    )
    def test_sends_welcome_dm_to_admin(
        self, mock_boto3, mock_store_cls, mock_encryptor_cls, mock_webclient_cls
    ):
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store
        mock_encryptor = MagicMock()
        mock_encryptor_cls.return_value = mock_encryptor
        mock_slack_client = MagicMock()
        mock_webclient_cls.return_value = mock_slack_client

        _save_workspace_config(self._TOKEN_RESPONSE)

        mock_webclient_cls.assert_called_once_with(token="xoxb-real-bot-token")
        mock_slack_client.chat_postMessage.assert_called_once()
        call_kwargs = mock_slack_client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "U_ADMIN_001"
        assert "Welcome to Sherpa" in call_kwargs["text"]
        assert "company website URL" in call_kwargs["text"]
