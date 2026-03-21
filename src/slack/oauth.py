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
from security.crypto import FieldEncryptor
from slack_sdk import WebClient
from state.dynamo import DynamoStateStore
from state.models import SetupState

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
        f"Sherpa installed successfully in {team_name}! " "You can close this tab.",
    )


def _exchange_code_for_token(code: str) -> dict[str, Any]:
    """Exchange an OAuth code for a bot token via Slack API."""
    # Get client credentials from Secrets Manager
    secret_arn = os.environ.get("APP_SECRETS_ARN", "")
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
    """Store workspace config, encrypted secrets, setup state and send welcome DM."""
    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "sherpa")
    kms_key_id = os.environ.get("KMS_KEY_ID", "")

    table = boto3.resource("dynamodb").Table(table_name)
    store = DynamoStateStore(table=table)
    encryptor = FieldEncryptor(kms_key_id)

    team = token_response.get("team", {})
    workspace_id = team.get("id", "")
    bot_token = token_response.get("access_token", "")
    bot_user_id = token_response.get("bot_user_id", "")
    admin_user_id = token_response.get("authed_user", {}).get("id", "")

    # Save workspace config without bot_token (stored in SECRETS instead)
    store.save_workspace_config(
        workspace_id=workspace_id,
        team_name=team.get("name", ""),
        bot_user_id=bot_user_id,
        bot_token=None,
        admin_user_id=admin_user_id,
        setup_complete=False,
    )

    # Encrypt and store bot_token in SECRETS record
    store.save_workspace_secrets(
        workspace_id=workspace_id,
        secrets_blob={"bot_token": bot_token},
        encryptor=encryptor,
    )

    # Create initial SETUP record
    store.save_setup_state(
        setup_state=SetupState(
            workspace_id=workspace_id,
            step="welcome",
            admin_user_id=admin_user_id,
        )
    )

    # Send welcome DM to admin
    slack_client = WebClient(token=bot_token)
    slack_client.chat_postMessage(
        channel=admin_user_id,
        text=(
            "Welcome to Sherpa! Let's set up your workspace. "
            "Send me your company website URL to get started."
        ),
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
