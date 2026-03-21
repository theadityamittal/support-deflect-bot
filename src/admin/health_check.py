"""Pinecone Health Check Lambda — runs daily via EventBridge.

Verifies the Pinecone index is reachable and not paused.
Pinecone Starter tier pauses indexes after 3 weeks of inactivity.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

logger = logging.getLogger(__name__)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Check Pinecone index health."""
    index_name = os.environ.get("PINECONE_INDEX_NAME", "sherpa")

    try:
        pc = _get_pinecone_client()
        index = pc.Index(index_name)
        stats = index.describe_index_stats()

        vector_count = stats.total_vector_count
        logger.info(
            "Pinecone health check passed: index=%s vectors=%d",
            index_name,
            vector_count,
        )
        return {"status": "healthy", "vector_count": vector_count}

    except Exception as e:
        logger.error("Pinecone health check FAILED: %s", str(e))
        return {"status": "unhealthy", "error": str(e)}


def _get_pinecone_client() -> Any:
    """Get Pinecone client using API key from consolidated secret."""
    from pinecone import Pinecone

    secret_arn = os.environ.get("APP_SECRETS_ARN", "")
    api_key = ""

    if secret_arn:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_arn)
        secrets = json.loads(response["SecretString"])
        api_key = secrets.get("pinecone_api_key", "")
    else:
        api_key = os.environ.get("PINECONE_API_KEY", "")

    return Pinecone(api_key=api_key)
