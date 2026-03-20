# src/slack/handler.py
"""Slack Handler Lambda — entry point for all Slack HTTP events.

Routes:
- POST /slack/events — Slack Events API (messages, mentions, team_join)
- POST /slack/commands — Slash commands (/onboard-status, -help, -restart)
- POST /slack/interactions — Interactive component callbacks

Strategy:
1. Verify Slack signature (sync, <1ms)
2. Return 200 immediately for events
3. Run middleware chain + enqueue to SQS
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import parse_qs

import boto3
from slack.commands import handle_command
from slack.models import SlackCommand, SlackEvent, SQSMessage
from slack.signature import InvalidSignatureError, verify_slack_signature

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def _get_header(headers: dict[str, str], name: str) -> str:
    """Case-insensitive header lookup."""
    value = headers.get(name, "")
    if value:
        return value
    lower_name = name.lower()
    for key, val in headers.items():
        if key.lower() == lower_name:
            return val
    return ""


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Main Lambda handler for Slack events, commands, and interactions."""
    body_str = event.get("body", "")
    headers = event.get("headers", {})
    path = event.get("path", "")

    logger.debug(
        "lambda_handler invoked: path=%s, body_length=%d, header_keys=%s",
        path,
        len(body_str),
        list(headers.keys()),
    )
    logger.debug("body preview: %s", body_str[:500])

    # Verify Slack signature
    signing_secret = _get_signing_secret()
    logger.debug("signing_secret retrieved (length=%d)", len(signing_secret))
    timestamp = _get_header(headers, "X-Slack-Request-Timestamp")
    signature = _get_header(headers, "X-Slack-Signature")
    logger.debug(
        "timestamp=%s, signature=%s",
        timestamp,
        signature[:20] if signature else "(empty)",
    )
    try:
        verify_slack_signature(
            signing_secret=signing_secret,
            body=body_str,
            timestamp=timestamp,
            signature=signature,
        )
        logger.debug("Signature verified OK")
    except InvalidSignatureError as e:
        logger.warning("Invalid Slack signature: %s", e)
        return _json_response(401, {"error": "Invalid signature"})

    # Route by path
    if path == "/slack/commands":
        logger.debug("Routing to slash command handler")
        return _handle_slash_command(body_str)
    elif path == "/slack/interactions":
        logger.debug("Routing to interaction handler")
        return _handle_interaction(body_str)
    else:
        logger.debug("Routing to event handler")
        return _handle_event(body_str)


def _handle_event(body_str: str) -> dict[str, Any]:
    """Handle Slack Events API callbacks."""
    body = json.loads(body_str)

    # URL verification challenge
    if body.get("type") == "url_verification":
        logger.debug("URL verification challenge")
        return _json_response(200, {"challenge": body["challenge"]})

    # Parse event
    slack_event = SlackEvent.from_event_body(body)
    logger.debug(
        "Parsed event: type=%s, user=%s, channel=%s, text=%s",
        slack_event.event_type,
        slack_event.user_id,
        slack_event.channel_id,
        slack_event.text[:80] if slack_event.text else "(empty)",
    )

    # Run middleware chain
    chain = _build_middleware_chain(workspace_id=slack_event.workspace_id)
    result = chain.run(slack_event)
    logger.debug(
        "Middleware result: allowed=%s, reason=%s", result.allowed, result.reason
    )

    if not result.allowed:
        logger.info(
            "Event blocked by middleware: %s (reason: %s)",
            slack_event.event_id,
            result.reason,
        )
        if result.should_respond and result.reason and slack_event.channel_id:
            _send_ephemeral_rejection(
                workspace_id=slack_event.workspace_id,
                channel_id=slack_event.channel_id,
                user_id=slack_event.user_id,
                text=result.reason,
            )
        return _json_response(200, {"ok": True})

    # Enqueue to SQS
    sqs_msg = SQSMessage(
        version="1.0",
        event_id=slack_event.event_id,
        workspace_id=slack_event.workspace_id,
        user_id=slack_event.user_id,
        channel_id=slack_event.channel_id,
        event_type=slack_event.event_type,
        text=slack_event.text,
        timestamp=slack_event.timestamp,
        is_dm=slack_event.channel_id.startswith("D"),
        thread_ts=slack_event.thread_ts,
    )
    logger.debug("SQS message prepared: %s", json.dumps(sqs_msg.to_dict())[:300])
    _enqueue_to_sqs(sqs_msg)

    return _json_response(200, {"ok": True})


def _handle_slash_command(body_str: str) -> dict[str, Any]:
    """Handle slash commands (form-encoded body)."""
    try:
        body = json.loads(body_str)
    except json.JSONDecodeError:
        # Slash commands are form-encoded in production
        parsed = parse_qs(body_str)
        body = {k: v[0] for k, v in parsed.items()}

    command = SlackCommand.from_command_body(body)
    state_store = _get_state_store()
    result: dict[str, Any] = handle_command(command, state_store=state_store)
    return result


def _handle_interaction(body_str: str) -> dict[str, Any]:
    """Handle interactive component callbacks (buttons, modals).

    Stub for Phase 2 — will be implemented when agent brain is added.
    """
    return _json_response(200, {"ok": True})


def _build_middleware_chain(*, workspace_id: str) -> Any:
    """Build the inbound middleware chain with real dependencies."""
    from middleware.inbound.chain import InboundMiddlewareChain

    state_store = _get_state_store()
    config = state_store.get_workspace_config(workspace_id=workspace_id)
    bot_user_id = config.bot_user_id if config else ""
    return InboundMiddlewareChain(state_store=state_store, bot_user_id=bot_user_id)


def _get_state_store() -> Any:
    """Get or create the DynamoDB state store."""
    from state.dynamo import DynamoStateStore

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "onboard-assist")
    table = boto3.resource("dynamodb").Table(table_name)
    return DynamoStateStore(table=table)


def _get_signing_secret() -> str:
    """Retrieve Slack signing secret from Secrets Manager."""
    secret_arn = os.environ.get("APP_SECRETS_ARN", "")
    if not secret_arn:
        return os.environ.get("SLACK_SIGNING_SECRET", "")

    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    secret_str: str = response["SecretString"]
    try:
        secret_data = json.loads(secret_str)
        return str(secret_data.get("signing_secret", secret_str))
    except json.JSONDecodeError:
        return secret_str


def _send_ephemeral_rejection(
    *,
    workspace_id: str,
    channel_id: str,
    user_id: str,
    text: str,
) -> None:
    """Send an ephemeral rejection message to the user."""
    state_store = _get_state_store()
    config = state_store.get_workspace_config(workspace_id=workspace_id)
    if not config or not config.bot_token:
        logger.warning(
            "No bot_token for workspace %s, skipping ephemeral", workspace_id
        )
        return
    try:
        from slack_sdk import WebClient

        client = WebClient(token=config.bot_token)
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=text,
        )
    except Exception:
        logger.exception("Failed to send ephemeral rejection")


def _enqueue_to_sqs(msg: SQSMessage) -> None:
    """Send a normalized message to the SQS FIFO queue."""
    queue_url = os.environ.get("SQS_QUEUE_URL", "")
    if not queue_url:
        logger.error("SQS_QUEUE_URL not set")
        return

    sqs = boto3.client("sqs")
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(msg.to_dict()),
        MessageGroupId=f"{msg.workspace_id}#{msg.user_id}",
        MessageDeduplicationId=msg.event_id,
    )
    logger.info("Enqueued event %s to SQS", msg.event_id)


def _json_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Build an API Gateway proxy response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
