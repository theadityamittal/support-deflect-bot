"""Shared test fixtures for all test modules."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    """Clear the settings LRU cache between tests."""
    yield
    try:
        from config.settings import get_settings

        get_settings.cache_clear()
    except ImportError:
        pass


@pytest.fixture
def mock_dynamodb_table():
    """Return a mock DynamoDB Table resource."""
    return MagicMock()


@pytest.fixture
def mock_s3_client():
    """Return a mock S3 client."""
    return MagicMock()


@pytest.fixture
def sample_plan_item():
    """Return a sample DynamoDB plan item for testing."""
    return {
        "pk": "WORKSPACE#W456",
        "sk": "PLAN#U123",
        "workspace_id": "W456",
        "user_id": "U123",
        "user_name": "Jane Smith",
        "role": "events",
        "status": "in_progress",
        "plan": {
            "version": 1,
            "steps": [
                {"id": 1, "title": "Welcome", "status": "completed", "summary": "Done"},
                {"id": 2, "title": "Intake", "status": "in_progress"},
                {"id": 3, "title": "Training", "status": "pending"},
            ],
        },
        "context": {
            "key_facts": ["2 years experience", "prefers mornings"],
            "recent_messages": [],
        },
    }


@pytest.fixture
def env_vars(monkeypatch):
    """Set required environment variables for Settings."""
    monkeypatch.setenv("PINECONE_INDEX_NAME", "test-index")
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test-table")
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
