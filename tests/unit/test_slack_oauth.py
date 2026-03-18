"""Tests for Slack OAuth callback Lambda."""

from __future__ import annotations

from unittest.mock import patch

from slack.oauth import lambda_handler


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
