"""Agent Worker Lambda — processes SQS messages and runs the orchestrator."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from slack.client import SlackClient
from slack_sdk import WebClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

_cached_secrets: dict[str, str] | None = None


def _get_app_secrets() -> dict[str, str]:
    """Read consolidated secrets from Secrets Manager. Cached per cold start."""
    global _cached_secrets  # noqa: PLW0603
    if _cached_secrets is not None:
        return _cached_secrets

    secret_arn = os.environ.get("APP_SECRETS_ARN", "")
    logger.debug(
        "_get_app_secrets: APP_SECRETS_ARN=%s",
        secret_arn[:20] if secret_arn else "(empty)",
    )
    if not secret_arn:
        msg = "APP_SECRETS_ARN not set"
        raise ValueError(msg)

    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    _cached_secrets = json.loads(response["SecretString"])
    logger.debug("_get_app_secrets: loaded %d keys", len(_cached_secrets))
    return _cached_secrets


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process an SQS message: parse → SETUP check → orchestrate → respond via Slack."""
    logger.debug(
        "lambda_handler invoked with %d records", len(event.get("Records", []))
    )
    for record in event.get("Records", []):
        try:
            body_raw = record["body"]
            logger.debug("SQS record body: %s", body_raw[:500])
            message = json.loads(body_raw)
            workspace_id = message["workspace_id"]
            user_id = message["user_id"]
            channel_id = message["channel_id"]
            text = message["text"]
            event_type = message.get("event_type", "message")
            metadata = message.get("metadata") or {}
            action_id = metadata.get("action_id")

            logger.info(
                "Processing message workspace=%s user=%s channel=%s event_type=%s text=%s",
                workspace_id,
                user_id,
                channel_id,
                event_type,
                text[:80],
            )

            logger.debug("Fetching bot token for workspace=%s", workspace_id)
            bot_token = _get_bot_token(workspace_id)
            logger.debug("Bot token retrieved (length=%d)", len(bot_token))

            slack_client = SlackClient(web_client=WebClient(token=bot_token))
            logger.debug("SlackClient created")

            # Check for active SETUP record — route to setup state machine if present
            setup_state = _get_setup_state(workspace_id=workspace_id)
            if setup_state is not None:
                logger.info(
                    "SETUP record found for workspace=%s, admin=%s",
                    workspace_id,
                    setup_state.admin_user_id,
                )
                try:
                    if user_id == setup_state.admin_user_id:
                        logger.debug(
                            "Routing admin user=%s to setup state machine", user_id
                        )
                        _call_process_setup_message(
                            text=text,
                            action_id=action_id,
                            setup_state=setup_state,
                            slack_client=slack_client,
                            workspace_id=workspace_id,
                        )
                    else:
                        logger.debug(
                            "Non-admin user=%s during setup, sending ephemeral", user_id
                        )
                        slack_client.send_ephemeral(
                            channel=channel_id,
                            user=user_id,
                            text="Setup is in progress. Please wait for the admin to complete workspace configuration.",
                        )
                finally:
                    _release_user_lock(workspace_id=workspace_id, user_id=user_id)
                continue

            logger.debug("Creating orchestrator")
            orchestrator = _create_orchestrator(
                workspace_id=workspace_id,
                user_id=user_id,
                channel_id=channel_id,
                slack_client=slack_client,
            )
            logger.debug("Orchestrator created successfully")

            try:
                logger.debug("Starting process_turn")
                response_text = orchestrator.process_turn(user_message=text)
                logger.debug(
                    "process_turn complete, response length=%d, preview=%s",
                    len(response_text),
                    response_text[:200],
                )

                logger.debug("Sending Slack message to channel=%s", channel_id)
                slack_client.send_message(channel=channel_id, text=response_text)

                logger.info("Response sent to %s/%s", workspace_id, user_id)
            finally:
                _release_user_lock(workspace_id=workspace_id, user_id=user_id)

        except Exception:
            logger.exception("Failed to process SQS message")
            return {"statusCode": 500, "body": "Processing failed"}

    return {"statusCode": 200, "body": "OK"}


def _release_user_lock(*, workspace_id: str, user_id: str) -> None:
    """Release the per-user processing lock in DynamoDB."""
    try:
        from state.dynamo import DynamoStateStore

        table_name = os.environ.get("DYNAMODB_TABLE_NAME", "onboard-assist")
        table = boto3.resource("dynamodb").Table(table_name)
        store = DynamoStateStore(table=table)
        store.release_lock(workspace_id=workspace_id, user_id=user_id)
        logger.debug("Released lock for workspace=%s user=%s", workspace_id, user_id)
    except Exception:
        logger.exception(
            "Failed to release lock for workspace=%s user=%s", workspace_id, user_id
        )


def _get_bot_token(workspace_id: str) -> str:
    """Get bot token: consolidated secret → DynamoDB SECRETS → WorkspaceConfig (with migration)."""
    # 1. Try consolidated Secrets Manager secret (deployment-time secret)
    try:
        secrets = _get_app_secrets()
        token = secrets.get("bot_token", "")
        if token and token != "placeholder":
            logger.debug("_get_bot_token: found in consolidated secret")
            return str(token)
    except ValueError:
        logger.debug("_get_bot_token: APP_SECRETS_ARN not set, trying DynamoDB")

    # 2. Try DynamoDB SECRETS record (per-workspace, post-OAuth) with lazy migration fallback
    from security.crypto import FieldEncryptor
    from state.dynamo import DynamoStateStore

    kms_key_id = os.environ.get("KMS_KEY_ID", "")
    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "onboard-assist")
    table = boto3.resource("dynamodb").Table(table_name)
    store = DynamoStateStore(table=table)

    if kms_key_id:
        try:
            encryptor = FieldEncryptor(kms_key_id=kms_key_id)
            token = store.get_bot_token(workspace_id=workspace_id, encryptor=encryptor)
            logger.debug("_get_bot_token: found via DynamoDB SECRETS/CONFIG")
            return token
        except ValueError:
            pass

    # 3. Fallback: plaintext WorkspaceConfig (no KMS available)
    config = store.get_workspace_config(workspace_id=workspace_id)
    if config and config.bot_token:
        logger.debug("_get_bot_token: found in plaintext workspace config")
        return str(config.bot_token)

    msg = f"No bot token found for workspace {workspace_id}"
    raise ValueError(msg)


def _get_setup_state(*, workspace_id: str) -> Any:
    """Return the active SETUP record for the workspace, or None if absent."""
    from state.dynamo import DynamoStateStore

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "onboard-assist")
    table = boto3.resource("dynamodb").Table(table_name)
    store = DynamoStateStore(table=table)
    result = store.get_setup_state(workspace_id=workspace_id)
    logger.debug(
        "_get_setup_state: workspace=%s found=%s", workspace_id, result is not None
    )
    return result


def _call_process_setup_message(
    *,
    text: str,
    action_id: str | None,
    setup_state: Any,
    slack_client: Any,
    workspace_id: str,
) -> None:
    """Build minimal SetupDependencies and delegate to process_setup_message."""
    from admin.setup import SetupDependencies, process_setup_message
    from state.dynamo import DynamoStateStore

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "onboard-assist")
    table = boto3.resource("dynamodb").Table(table_name)
    state_store = DynamoStateStore(table=table)

    sqs_queue_url = os.environ.get("SQS_QUEUE_URL", "")
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    google_oauth_redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "")

    deps = SetupDependencies(
        state_store=state_store,
        slack_client=slack_client,
        encryptor=None,
        sqs_queue_url=sqs_queue_url,
        google_client_id=google_client_id,
        google_oauth_redirect_uri=google_oauth_redirect_uri,
        lambda_context=None,
        sqs_client=boto3.client("sqs") if sqs_queue_url else None,
    )

    logger.debug(
        "_call_process_setup_message: workspace=%s step=%s action_id=%s",
        workspace_id,
        setup_state.step,
        action_id,
    )
    process_setup_message(
        text=text,
        action_id=action_id,
        setup_state=setup_state,
        deps=deps,
    )


def _create_orchestrator(
    *,
    workspace_id: str,
    user_id: str,
    channel_id: str,
    slack_client: SlackClient,
) -> Any:
    """Wire up the orchestrator with all dependencies."""
    logger.debug("_create_orchestrator: importing dependencies")
    from agent.orchestrator import Orchestrator
    from agent.tools.assign_channel import AssignChannelTool
    from agent.tools.calendar_event import CalendarEventTool
    from agent.tools.manage_progress import ManageProgressTool
    from agent.tools.search_kb import SearchKBTool
    from agent.tools.send_message import SendMessageTool
    from config.settings import get_settings
    from llm.gemini import GeminiProvider
    from llm.router import LLMRouter
    from middleware.agent.turn_budget import TurnBudgetEnforcer
    from rag.vectorstore import PineconeVectorStore
    from state.dynamo import DynamoStateStore

    logger.debug("_create_orchestrator: imports complete, loading settings")
    settings = get_settings()
    logger.debug(
        "_create_orchestrator: settings loaded — table=%s, pinecone_index=%s, region=%s",
        settings.dynamodb_table_name,
        settings.pinecone_index_name,
        settings.aws_region,
    )

    table = boto3.resource("dynamodb").Table(settings.dynamodb_table_name)
    state_store = DynamoStateStore(table=table)
    logger.debug("_create_orchestrator: DynamoDB state store ready")

    secrets = _get_app_secrets()
    provider = GeminiProvider(api_key=secrets["gemini_api_key"])
    router = LLMRouter(
        provider=provider,
        reasoning_model_id=settings.reasoning_model_id,
        generation_model_id=settings.generation_model_id,
    )
    logger.debug(
        "_create_orchestrator: LLM router ready — reasoning=%s, generation=%s",
        settings.reasoning_model_id,
        settings.generation_model_id,
    )

    logger.debug("_create_orchestrator: Slack client received")

    pinecone_key = secrets["pinecone_api_key"]
    logger.debug(
        "_create_orchestrator: initializing PineconeVectorStore index=%s",
        settings.pinecone_index_name,
    )
    vectorstore = PineconeVectorStore(
        api_key=pinecone_key, index_name=settings.pinecone_index_name
    )
    logger.debug("_create_orchestrator: Pinecone vectorstore ready")

    tools: dict[str, Any] = {
        "search_kb": SearchKBTool(vectorstore=vectorstore, namespace=workspace_id),
        "send_message": SendMessageTool(
            slack_client=slack_client, channel_id=channel_id
        ),
        "assign_channel": AssignChannelTool(slack_client=slack_client, user_id=user_id),
        "calendar_event": CalendarEventTool(),
        "manage_progress": ManageProgressTool(
            state_store=state_store,
            workspace_id=workspace_id,
            user_id=user_id,
            router=router,
        ),
    }
    logger.debug(
        "_create_orchestrator: %d tools registered: %s", len(tools), list(tools.keys())
    )

    budget = TurnBudgetEnforcer(
        max_reasoning_calls=settings.max_reasoning_calls_per_turn,
        max_generation_calls=settings.max_generation_calls_per_turn,
        max_tool_calls=settings.max_tool_calls_per_turn,
        max_output_tokens=settings.max_total_output_tokens_per_turn,
    )
    logger.debug("_create_orchestrator: budget enforcer ready")

    return Orchestrator(
        router=router,
        state_store=state_store,
        tools=tools,
        workspace_id=workspace_id,
        user_id=user_id,
        channel_id=channel_id,
        budget=budget,
    )
