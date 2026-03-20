"""Google OAuth callback Lambda.

Handles the OAuth2 redirect after an admin authorizes calendar access.
Exchanges the authorization code for tokens, stores them encrypted,
updates workspace config, notifies the admin via DM, and enqueues an
SQS continuation message.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, cast

import boto3
from gcal.client import GoogleCalendarClient
from security.crypto import FieldEncryptor
from slack.client import SlackClient
from slack_sdk import WebClient
from state.dynamo import DynamoStateStore

logger = logging.getLogger(__name__)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle GET /google/oauth/callback."""
    params = event.get("queryStringParameters") or {}

    # User denied or cancelled access
    if params.get("error"):
        logger.info("Google OAuth cancelled: %s", params["error"])
        return _html_response(200, "Calendar setup cancelled. You can close this tab.")

    code = params.get("code", "")
    if not code:
        return _html_response(400, "Missing authorization code.")

    workspace_id = params.get("state", "")

    try:
        tokens = _exchange_code(code)
        _store_tokens(workspace_id=workspace_id, tokens=tokens)
        _set_calendar_enabled(workspace_id=workspace_id)
        _notify_admin(workspace_id=workspace_id)
        _enqueue_continuation(workspace_id=workspace_id)
    except Exception:
        logger.exception("Google OAuth callback failed for workspace %s", workspace_id)
        return _html_response(
            500, "An error occurred during calendar setup. Please try again."
        )

    return _html_response(
        200,
        "Google Calendar connected successfully! You can close this tab.",
    )


def _exchange_code(code: str) -> dict[str, Any]:
    """Exchange the authorization code for access + refresh tokens."""
    secrets = _get_app_secrets()
    client = GoogleCalendarClient(
        client_id=secrets.get(
            "google_client_id", os.environ.get("GOOGLE_CLIENT_ID", "")
        ),
        client_secret=secrets.get(
            "google_client_secret", os.environ.get("GOOGLE_CLIENT_SECRET", "")
        ),
    )
    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "")
    return cast(
        dict[str, Any], client.exchange_code(code=code, redirect_uri=redirect_uri)
    )


def _store_tokens(*, workspace_id: str, tokens: dict[str, Any]) -> None:
    """Encrypt and persist Google tokens into the SECRETS record."""
    encryptor = FieldEncryptor(kms_key_id=os.environ["KMS_KEY_ID"])
    store = _get_store()

    existing = (
        store.get_workspace_secrets(workspace_id=workspace_id, encryptor=encryptor)
        or {}
    )

    # Calculate expiry epoch from expires_in seconds
    expires_in = tokens.get("expires_in", 0)
    access_token_expiry = str(int(time.time()) + int(expires_in)) if expires_in else ""

    updated = {
        **existing,
        "google_refresh_token": tokens.get("refresh_token", ""),
        "google_access_token": tokens.get("access_token", ""),
        "access_token_expiry": access_token_expiry,
    }

    store.save_workspace_secrets(
        workspace_id=workspace_id,
        secrets_blob=updated,
        encryptor=encryptor,
    )


def _set_calendar_enabled(*, workspace_id: str) -> None:
    """Update WorkspaceConfig to mark calendar_enabled=True."""
    store = _get_store()
    config = store.get_workspace_config(workspace_id=workspace_id)
    if config is None:
        logger.warning(
            "No WorkspaceConfig found for workspace %s; skipping calendar_enabled update",
            workspace_id,
        )
        return

    store.save_workspace_config(
        workspace_id=workspace_id,
        team_name=config.team_name,
        bot_user_id=config.bot_user_id,
        bot_token=config.bot_token,
        admin_user_id=config.admin_user_id,
        setup_complete=config.setup_complete,
        website_url=config.website_url,
        teams=config.teams,
        channel_mapping=dict(config.channel_mapping),
        calendar_enabled=True,
    )


def _notify_admin(*, workspace_id: str) -> None:
    """Send a DM to the workspace admin confirming calendar connection."""
    store = _get_store()
    config = store.get_workspace_config(workspace_id=workspace_id)
    if config is None or not config.admin_user_id:
        logger.warning(
            "No admin_user_id found for workspace %s; skipping DM", workspace_id
        )
        return

    encryptor = FieldEncryptor(kms_key_id=os.environ["KMS_KEY_ID"])
    bot_token = store.get_bot_token(workspace_id=workspace_id, encryptor=encryptor)

    slack_client = SlackClient(web_client=WebClient(token=bot_token))
    slack_client.send_message(
        channel=config.admin_user_id,
        text="Google Calendar connected successfully!",
    )


def _enqueue_continuation(*, workspace_id: str) -> None:
    """Enqueue an SQS message to continue the setup flow."""
    queue_url = os.environ.get("SQS_QUEUE_URL", "")
    if not queue_url:
        logger.warning("SQS_QUEUE_URL not set; skipping continuation enqueue")
        return

    sqs = boto3.client("sqs")
    timestamp = int(time.time())
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(
            {
                "type": "gcal_oauth_complete",
                "workspace_id": workspace_id,
                "timestamp": timestamp,
            }
        ),
        MessageGroupId=workspace_id,
        MessageDeduplicationId=f"gcal-oauth-{workspace_id}-{timestamp}",
    )


def _get_app_secrets() -> dict[str, Any]:
    """Retrieve the consolidated app secrets from Secrets Manager."""
    secret_arn = os.environ.get("APP_SECRETS_ARN", "")
    if not secret_arn:
        return {}
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    result: dict[str, Any] = json.loads(response["SecretString"])
    return result


def _get_store() -> Any:
    """Create a DynamoStateStore backed by the configured table."""
    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "onboard-assist")
    table = boto3.resource("dynamodb").Table(table_name)
    return DynamoStateStore(table=table)


def _html_response(status_code: int, message: str) -> dict[str, Any]:
    """Build an API Gateway response with an HTML body."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "text/html"},
        "body": f"<html><body><h2>{message}</h2></body></html>",
    }
