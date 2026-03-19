"""Shared fixtures for E2E tests against deployed AWS infrastructure."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import os
import sys
import time
from typing import Any

import boto3
import pytest
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..", "..", "src")))

load_dotenv()

API_GATEWAY_URL = os.getenv(
    "API_GATEWAY_URL",
    "https://w7x89hdhpj.execute-api.us-east-1.amazonaws.com/prod",
)
SQS_QUEUE_URL = os.getenv(
    "SQS_QUEUE_URL",
    "https://sqs.us-east-1.amazonaws.com/962273458473/onboard-assist-queue.fifo",
)
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE_NAME", "onboard-assist")
S3_BUCKET = os.getenv("S3_BUCKET_NAME", "onboard-assist-docs-962273458473")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "onboard-assist")
SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")

E2E_WORKSPACE_ID = "E2E_TEST_WORKSPACE"


@pytest.fixture()
def api_base_url() -> str:
    return API_GATEWAY_URL


@pytest.fixture()
def signing_secret() -> str:
    if not SIGNING_SECRET:
        pytest.skip("SLACK_SIGNING_SECRET not set")
    return SIGNING_SECRET


@pytest.fixture()
def pinecone_store():
    if not PINECONE_API_KEY:
        pytest.skip("PINECONE_API_KEY not set")
    from rag.vectorstore import PineconeVectorStore

    return PineconeVectorStore(api_key=PINECONE_API_KEY, index_name=PINECONE_INDEX_NAME)


@pytest.fixture()
def test_namespace(pinecone_store):
    ns = f"e2e-test-{int(time.time())}"
    yield ns
    # Cleanup: delete all vectors in the test namespace
    with contextlib.suppress(Exception):
        pinecone_store.delete_namespace(namespace=ns)


@pytest.fixture()
def s3_storage():
    from rag.storage import S3Storage

    return S3Storage(bucket_name=S3_BUCKET)


@pytest.fixture()
def s3_test_namespace(s3_storage):
    ns = f"e2e-test-{int(time.time())}"
    yield ns
    # Cleanup: delete test objects from S3
    s3 = boto3.client("s3")
    try:
        response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{ns}/")
        for obj in response.get("Contents", []):
            s3.delete_object(Bucket=S3_BUCKET, Key=obj["Key"])
    except Exception:
        pass


@pytest.fixture()
def sqs_queue_url() -> str:
    return SQS_QUEUE_URL


@pytest.fixture()
def dynamodb_table():
    return boto3.resource("dynamodb").Table(DYNAMODB_TABLE)


def sign_request(body_str: str, secret: str) -> dict[str, str]:
    """Compute Slack HMAC-SHA256 signature headers."""
    ts = str(int(time.time()))
    sig_basestring = f"v0:{ts}:{body_str}"
    signature = (
        "v0="
        + hmac.new(secret.encode(), sig_basestring.encode(), hashlib.sha256).hexdigest()
    )
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": signature,
        "Content-Type": "application/json",
    }


def get_sqs_depth(queue_url: str) -> int:
    """Get approximate number of messages in SQS queue."""
    sqs = boto3.client("sqs")
    attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages"],
    )
    return int(attrs["Attributes"]["ApproximateNumberOfMessages"])


def cleanup_dynamodb_test_records(table: Any) -> None:
    """Delete any DynamoDB records created by E2E tests."""
    response = table.scan(
        FilterExpression="begins_with(pk, :prefix)",
        ExpressionAttributeValues={":prefix": f"WORKSPACE#{E2E_WORKSPACE_ID}"},
    )
    for item in response.get("Items", []):
        table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
