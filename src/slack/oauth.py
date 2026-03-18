"""Slack OAuth callback Lambda.

Handles the OAuth2 redirect after a workspace admin clicks "Add to Slack".
Exchanges the auth code for a bot token and stores it in DynamoDB.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

logger = logging.getLogger(__name__)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle GET /slack/oauth/callback."""
    params = event.get("queryStringParameters") or {}

    # User denied access
    if params.get("error"):
        return _html_response(200, "Installation cancelled. You can close this tab.")

    code = params.get("code", "")
    if not code:
        return _html_response(400, "Missing authorization code.")

    # Exchange code for token
    token_response = _exchange_code_for_token(code)

    if not token_response.get("ok"):
        error = token_response.get("error", "unknown")
        logger.error("OAuth token exchange failed: %s", error)
        return _html_response(400, f"Installation failed: {error}")

    # Save workspace config
    _save_workspace_config(token_response)

    team_name = token_response.get("team", {}).get("name", "your workspace")
    return _html_response(
        200,
        f"Onboard Assist installed successfully in {team_name}! "
        "You can close this tab.",
    )


def _exchange_code_for_token(code: str) -> dict[str, Any]:
    """Exchange an OAuth code for a bot token via Slack API."""
    from slack_sdk import WebClient

    # Get client credentials from Secrets Manager
    secret_arn = os.environ.get("SLACK_SIGNING_SECRET_ARN", "")
    secrets = _get_secret(secret_arn) if secret_arn else {}

    client = WebClient()
    response = client.oauth_v2_access(
        client_id=secrets.get("client_id", os.environ.get("SLACK_CLIENT_ID", "")),
        client_secret=secrets.get(
            "client_secret", os.environ.get("SLACK_CLIENT_SECRET", "")
        ),
        code=code,
    )
    result: dict[str, Any] = dict(response)
    return result


def _save_workspace_config(token_response: dict[str, Any]) -> None:
    """Store workspace bot token in DynamoDB."""
    from state.dynamo import DynamoStateStore

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "onboard-assist")
    table = boto3.resource("dynamodb").Table(table_name)
    store = DynamoStateStore(table=table)

    team = token_response.get("team", {})
    store.save_workspace_config(
        workspace_id=team.get("id", ""),
        team_name=team.get("name", ""),
        bot_token=token_response.get("access_token", ""),
        bot_user_id=token_response.get("bot_user_id", ""),
    )


def _get_secret(secret_arn: str) -> dict[str, Any]:
    """Retrieve a JSON secret from Secrets Manager."""
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    result: dict[str, Any] = json.loads(response["SecretString"])
    return result


def _html_response(status_code: int, message: str) -> dict[str, Any]:
    """Build an API Gateway response with HTML body."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "text/html"},
        "body": f"<html><body><h2>{message}</h2></body></html>",
    }
