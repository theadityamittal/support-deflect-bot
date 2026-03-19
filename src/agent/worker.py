"""Agent Worker Lambda — processes SQS messages and runs the orchestrator."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

logger = logging.getLogger(__name__)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process an SQS message: parse → orchestrate → respond via Slack."""
    for record in event.get("Records", []):
        try:
            message = json.loads(record["body"])
            workspace_id = message["workspace_id"]
            user_id = message["user_id"]
            channel_id = message["channel_id"]
            text = message["text"]

            logger.info(
                "Processing message from %s/%s: %s",
                workspace_id,
                user_id,
                text[:50],
            )

            bot_token = _get_bot_token(workspace_id)
            orchestrator = _create_orchestrator(
                workspace_id=workspace_id,
                user_id=user_id,
                channel_id=channel_id,
                bot_token=bot_token,
            )

            response_text = orchestrator.process_turn(user_message=text)

            _send_slack_message(
                bot_token=bot_token,
                channel_id=channel_id,
                text=response_text,
            )

            logger.info("Response sent to %s/%s", workspace_id, user_id)

        except Exception:
            logger.exception("Failed to process SQS message")
            return {"statusCode": 500, "body": "Processing failed"}

    return {"statusCode": 200, "body": "OK"}


def _get_bot_token(workspace_id: str) -> str:
    """Get bot token from Secrets Manager or DynamoDB workspace config."""
    secret_arn = os.environ.get("SLACK_SIGNING_SECRET_ARN", "")
    if secret_arn:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_arn)
        secret = json.loads(response["SecretString"])
        token = secret.get("bot_token", "")
        if token and token != "placeholder":
            return str(token)

    from state.dynamo import DynamoStateStore

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "onboard-assist")
    table = boto3.resource("dynamodb").Table(table_name)
    store = DynamoStateStore(table=table)
    config = store.get_workspace_config(workspace_id=workspace_id)
    if config:
        return str(config.bot_token)

    msg = f"No bot token found for workspace {workspace_id}"
    raise ValueError(msg)


def _create_orchestrator(
    *, workspace_id: str, user_id: str, channel_id: str, bot_token: str
) -> Any:
    """Wire up the orchestrator with all dependencies."""
    from agent.orchestrator import Orchestrator
    from agent.tools.assign_channel import AssignChannelTool
    from agent.tools.calendar_event import CalendarEventTool
    from agent.tools.manage_progress import ManageProgressTool
    from agent.tools.search_kb import SearchKBTool
    from agent.tools.send_message import SendMessageTool
    from config.settings import get_settings
    from llm.bedrock import BedrockProvider
    from llm.router import LLMRouter
    from middleware.agent.turn_budget import TurnBudgetEnforcer
    from rag.vectorstore import PineconeVectorStore
    from slack.client import SlackClient
    from slack_sdk import WebClient
    from state.dynamo import DynamoStateStore

    settings = get_settings()
    table = boto3.resource("dynamodb").Table(settings.dynamodb_table_name)
    state_store = DynamoStateStore(table=table)

    provider = BedrockProvider(region=settings.aws_region)
    router = LLMRouter(
        provider=provider,
        reasoning_model_id=settings.reasoning_model_id,
        generation_model_id=settings.generation_model_id,
    )

    web_client = WebClient(token=bot_token)
    slack_client = SlackClient(web_client=web_client)

    pinecone_key = os.environ.get("PINECONE_API_KEY", "")
    vectorstore = PineconeVectorStore(
        api_key=pinecone_key, index_name=settings.pinecone_index_name
    )

    tools: dict[str, Any] = {
        "search_kb": SearchKBTool(vectorstore=vectorstore, namespace=workspace_id),
        "send_message": SendMessageTool(
            slack_client=slack_client, channel_id=channel_id
        ),
        "assign_channel": AssignChannelTool(web_client=web_client, user_id=user_id),
        "calendar_event": CalendarEventTool(),
        "manage_progress": ManageProgressTool(
            state_store=state_store,
            workspace_id=workspace_id,
            user_id=user_id,
            router=router,
        ),
    }

    budget = TurnBudgetEnforcer(
        max_reasoning_calls=settings.max_reasoning_calls_per_turn,
        max_generation_calls=settings.max_generation_calls_per_turn,
        max_tool_calls=settings.max_tool_calls_per_turn,
        max_output_tokens=settings.max_total_output_tokens_per_turn,
    )

    return Orchestrator(
        router=router,
        state_store=state_store,
        tools=tools,
        workspace_id=workspace_id,
        user_id=user_id,
        channel_id=channel_id,
        budget=budget,
    )


def _send_slack_message(*, bot_token: str, channel_id: str, text: str) -> None:
    """Send a message via Slack API."""
    from slack_sdk import WebClient

    client = WebClient(token=bot_token)
    client.chat_postMessage(channel=channel_id, text=text)
