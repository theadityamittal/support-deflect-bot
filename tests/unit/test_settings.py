"""Tests for configuration settings module."""

import pytest
from pydantic import ValidationError


class TestSettings:
    """Test Pydantic Settings configuration."""

    def test_default_settings_load(self, monkeypatch):
        """Settings load with required env vars."""
        monkeypatch.setenv("PINECONE_INDEX_NAME", "sherpa")
        monkeypatch.setenv("DYNAMODB_TABLE_NAME", "sherpa")
        monkeypatch.setenv("S3_BUCKET_NAME", "sherpa-docs")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        from config.settings import Settings

        settings = Settings()
        assert settings.pinecone_index_name == "sherpa"
        assert settings.dynamodb_table_name == "sherpa"
        assert settings.s3_bucket_name == "sherpa-docs"
        assert settings.aws_region == "us-east-1"

    def test_chunk_size_defaults(self, monkeypatch):
        """Chunk settings have sensible defaults."""
        monkeypatch.setenv("PINECONE_INDEX_NAME", "test")
        monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test")
        monkeypatch.setenv("S3_BUCKET_NAME", "test")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        from config.settings import Settings

        settings = Settings()
        assert settings.chunk_size == 512
        assert settings.chunk_overlap == 50
        assert 0 < settings.min_confidence <= 1.0

    def test_cost_cap_defaults(self, monkeypatch):
        """Cost caps have the values from spec."""
        monkeypatch.setenv("PINECONE_INDEX_NAME", "test")
        monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test")
        monkeypatch.setenv("S3_BUCKET_NAME", "test")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        from config.settings import Settings

        settings = Settings()
        assert settings.max_turns_per_day == 50
        assert settings.max_workspace_monthly_cost == 5.0
        assert settings.kill_switch_threshold == 5.0

    def test_per_turn_budget_defaults(self, monkeypatch):
        """Per-turn budget limits match spec."""
        monkeypatch.setenv("PINECONE_INDEX_NAME", "test")
        monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test")
        monkeypatch.setenv("S3_BUCKET_NAME", "test")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        from config.settings import Settings

        settings = Settings()
        assert settings.max_reasoning_calls_per_turn == 3
        assert settings.max_generation_calls_per_turn == 1
        assert settings.max_tool_calls_per_turn == 4
        assert settings.max_total_output_tokens_per_turn == 5000

    def test_settings_are_immutable(self, monkeypatch):
        """Settings should not be mutated after creation."""
        monkeypatch.setenv("PINECONE_INDEX_NAME", "test")
        monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test")
        monkeypatch.setenv("S3_BUCKET_NAME", "test")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        from config.settings import Settings

        settings = Settings()
        with pytest.raises(ValidationError):
            settings.chunk_size = 9999

    def test_missing_required_env_var_raises(self, monkeypatch, tmp_path):
        """Missing required env vars raise validation error."""
        monkeypatch.delenv("PINECONE_INDEX_NAME", raising=False)
        monkeypatch.delenv("DYNAMODB_TABLE_NAME", raising=False)
        monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
        monkeypatch.chdir(tmp_path)  # avoid loading .env from project root

        from config.settings import Settings

        with pytest.raises(ValidationError):
            Settings()

    def test_get_settings_returns_cached_instance(self, monkeypatch):
        """get_settings returns the same instance (cached)."""
        monkeypatch.setenv("PINECONE_INDEX_NAME", "test")
        monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test")
        monkeypatch.setenv("S3_BUCKET_NAME", "test")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        from config.settings import get_settings

        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_slack_settings_defaults(self, monkeypatch):
        """New Slack settings should exist with correct defaults."""
        monkeypatch.setenv("PINECONE_INDEX_NAME", "test")
        monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test")
        monkeypatch.setenv("S3_BUCKET_NAME", "test")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        monkeypatch.setenv("SQS_QUEUE_URL", "")
        monkeypatch.setenv("API_GATEWAY_ID", "")
        monkeypatch.setenv("APP_SECRETS_ARN", "")

        from config.settings import Settings

        settings = Settings()
        assert settings.app_secrets_arn == ""
        assert settings.rate_limit_window_seconds == 60
        assert settings.max_message_length == 4000
        assert settings.injection_strike_limit == 3
        assert settings.sqs_queue_url == ""
        assert settings.api_gateway_id == ""
