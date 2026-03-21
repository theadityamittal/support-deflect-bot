"""Kill Switch Lambda — triggered by SNS budget alarm.

When AWS Budgets triggers the alarm at $10/mo:
1. Set SYSTEM#KILL_SWITCH in DynamoDB
2. Disable API Gateway stage
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3

logger = logging.getLogger(__name__)


def lambda_handler(event: dict[str, Any], context: Any) -> None:
    """Handle SNS notification from AWS Budgets."""
    records = event.get("Records", [])
    if not records:
        logger.warning("Kill switch invoked with no records")
        return

    logger.critical(
        "KILL SWITCH ACTIVATED — budget alarm triggered with %d records",
        len(records),
    )

    # Set DynamoDB kill switch flag
    store = _get_state_store()
    store.set_kill_switch(active=True)

    # Disable API Gateway
    _disable_api_gateway()

    logger.critical("Kill switch complete — API Gateway disabled, DynamoDB flag set")


def _get_state_store() -> Any:
    """Get DynamoDB state store."""
    from state.dynamo import DynamoStateStore

    table_name = os.environ.get("DYNAMODB_TABLE_NAME", "sherpa")
    table = boto3.resource("dynamodb").Table(table_name)
    return DynamoStateStore(table=table)


def _disable_api_gateway() -> None:
    """Disable the API Gateway prod stage."""
    api_id = os.environ.get("API_GATEWAY_ID", "")
    if not api_id:
        logger.error("API_GATEWAY_ID not set — cannot disable API Gateway")
        return

    client = boto3.client("apigateway")
    client.update_stage(
        restApiId=api_id,
        stageName="prod",
        patchOperations=[
            {
                "op": "replace",
                "path": "/*/*/throttling/rateLimit",
                "value": "0",
            },
        ],
    )
    logger.info("API Gateway %s prod stage throttled to 0", api_id)
