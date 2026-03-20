"""Application configuration via environment variables.

Uses pydantic-settings to load, validate, and freeze all config.
Every setting comes from env vars — no config files.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Immutable application settings loaded from environment variables."""

    model_config = {
        "frozen": True,
        "env_file": ".env",
        "extra": "ignore",
        "populate_by_name": True,
    }

    # --- AWS ---
    aws_region: str = Field(alias="AWS_DEFAULT_REGION", default="us-east-1")

    # --- Pinecone ---
    pinecone_index_name: str = Field(alias="PINECONE_INDEX_NAME")

    # --- DynamoDB ---
    dynamodb_table_name: str = Field(alias="DYNAMODB_TABLE_NAME")

    # --- S3 ---
    s3_bucket_name: str = Field(alias="S3_BUCKET_NAME")

    # --- RAG ---
    chunk_size: int = Field(default=512, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=50, alias="CHUNK_OVERLAP")
    min_confidence: float = Field(default=0.3, alias="MIN_CONFIDENCE")
    max_chunks: int = Field(default=10, alias="MAX_CHUNKS")

    # --- LLM ---
    reasoning_model_id: str = Field(
        default="gemini-2.5-flash-lite", alias="REASONING_MODEL_ID"
    )
    generation_model_id: str = Field(
        default="gemini-2.5-flash",
        alias="GENERATION_MODEL_ID",
    )

    # --- Per-Turn Budget (Layer 1) ---
    max_reasoning_calls_per_turn: int = Field(default=3)
    max_generation_calls_per_turn: int = Field(default=1)
    max_tool_calls_per_turn: int = Field(default=4)
    max_total_output_tokens_per_turn: int = Field(default=5000)

    # --- Per-User Daily Budget (Layer 2) ---
    max_turns_per_day: int = Field(default=50)
    max_output_tokens_per_day: int = Field(default=50000)
    max_tool_calls_per_day: int = Field(default=100)

    # --- Per-Workspace Monthly Budget (Layer 3) ---
    max_workspace_monthly_cost: float = Field(default=5.0)
    max_workspace_monthly_tokens: int = Field(default=500000)

    # --- Kill Switch ---
    kill_switch_threshold: float = Field(default=5.0)
    kill_switch_cache_ttl_seconds: int = Field(default=60)

    # --- Agent Worker ---
    agent_worker_reserved_concurrency: int = Field(default=5)
    sqs_visibility_timeout: int = Field(default=900)

    # --- Slack ---
    app_secrets_arn: str = Field(default="", alias="APP_SECRETS_ARN")
    rate_limit_window_seconds: int = Field(default=60)
    max_message_length: int = Field(default=4000)
    injection_strike_limit: int = Field(default=3)
    sqs_queue_url: str = Field(default="", alias="SQS_QUEUE_URL")
    api_gateway_id: str = Field(default="", alias="API_GATEWAY_ID")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance. Call once per Lambda cold start."""
    return Settings()  # type: ignore[call-arg]
