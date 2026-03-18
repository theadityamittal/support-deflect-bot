"""DynamoDB CRUD operations for single-table design."""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from typing import Any

from botocore.exceptions import ClientError
from state.models import CompletionRecord, OnboardingPlan, WorkspaceConfig
from state.ttl import ttl_for_injection_log, ttl_for_lock, ttl_for_plan

logger = logging.getLogger(__name__)


class DynamoStateStore:
    """DynamoDB operations using single-table design.

    All operations use pk/sk access patterns.
    """

    def __init__(self, *, table: Any) -> None:
        """Initialize with a boto3 DynamoDB Table resource."""
        self._table = table

    def get_plan(self, *, workspace_id: str, user_id: str) -> OnboardingPlan | None:
        """Retrieve an active onboarding plan."""
        response = self._table.get_item(
            Key={
                "pk": f"WORKSPACE#{workspace_id}",
                "sk": f"PLAN#{user_id}",
            }
        )
        item = response.get("Item")
        if not item:
            return None
        return OnboardingPlan.from_dynamo_item(item)

    def save_plan(self, plan: OnboardingPlan) -> None:
        """Save or update an onboarding plan with TTL."""
        item = plan.to_dynamo_item()
        item["ttl"] = ttl_for_plan()
        self._table.put_item(Item=item)

    def save_completion_record(self, record: CompletionRecord) -> None:
        """Save a completion record (no TTL -- permanent)."""
        item = record.to_dynamo_item()
        self._table.put_item(Item=item)

    def acquire_lock(self, *, workspace_id: str, user_id: str) -> bool:
        """Acquire a processing lock. Returns True if acquired, False if held."""
        try:
            self._table.put_item(
                Item={
                    "pk": f"WORKSPACE#{workspace_id}",
                    "sk": f"LOCK#{user_id}",
                    "ttl": ttl_for_lock(),
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def release_lock(self, *, workspace_id: str, user_id: str) -> None:
        """Release a processing lock."""
        self._table.delete_item(
            Key={
                "pk": f"WORKSPACE#{workspace_id}",
                "sk": f"LOCK#{user_id}",
            }
        )

    def get_kill_switch_status(self) -> bool:
        """Check if the global kill switch is active."""
        response = self._table.get_item(Key={"pk": "SYSTEM", "sk": "KILL_SWITCH"})
        item = response.get("Item")
        if not item:
            return False
        result: bool = item.get("active", False)
        return result

    def set_kill_switch(self, *, active: bool) -> None:
        """Set the global kill switch status."""
        self._table.put_item(
            Item={
                "pk": "SYSTEM",
                "sk": "KILL_SWITCH",
                "active": active,
                "updated_at": int(time.time()),
            }
        )

    def save_workspace_config(
        self,
        *,
        workspace_id: str,
        team_name: str,
        bot_token: str,
        bot_user_id: str,
    ) -> None:
        """Save workspace configuration (upsert)."""
        self._table.put_item(
            Item={
                "pk": f"WORKSPACE#{workspace_id}",
                "sk": "CONFIG",
                "workspace_id": workspace_id,
                "team_name": team_name,
                "bot_token": bot_token,
                "bot_user_id": bot_user_id,
                "active": True,
                "updated_at": int(time.time()),
            }
        )

    def get_workspace_config(self, *, workspace_id: str) -> WorkspaceConfig | None:
        """Retrieve workspace configuration."""
        response = self._table.get_item(
            Key={"pk": f"WORKSPACE#{workspace_id}", "sk": "CONFIG"}
        )
        item = response.get("Item")
        if not item:
            return None
        return WorkspaceConfig(
            workspace_id=item["workspace_id"],
            team_name=item.get("team_name", ""),
            bot_token=item.get("bot_token", ""),
            bot_user_id=item.get("bot_user_id", ""),
            active=item.get("active", True),
        )

    def get_daily_usage_turns(self, *, workspace_id: str, user_id: str) -> int:
        """Get turn count for today's usage record."""
        today = date.today().isoformat()
        response = self._table.get_item(
            Key={
                "pk": f"WORKSPACE#{workspace_id}",
                "sk": f"USAGE#{user_id}#{today}",
            }
        )
        item = response.get("Item")
        if not item:
            return 0
        result: int = int(item.get("turns", 0))
        return result

    def get_monthly_usage_cost(self, *, workspace_id: str) -> float:
        """Get estimated cost for this month's workspace usage."""
        month = date.today().strftime("%Y-%m")
        response = self._table.get_item(
            Key={
                "pk": f"WORKSPACE#{workspace_id}",
                "sk": f"USAGE#{month}",
            }
        )
        item = response.get("Item")
        if not item:
            return 0.0
        result: float = float(item.get("estimated_cost", 0.0))
        return result

    def log_injection_attempt(
        self,
        *,
        workspace_id: str,
        user_id: str,
        text: str,
    ) -> None:
        """Log an injection attempt to DynamoDB."""
        now = datetime.now(UTC)
        self._table.put_item(
            Item={
                "pk": "SECURITY",
                "sk": f"INJECTION#{now.isoformat()}#{user_id}",
                "workspace_id": workspace_id,
                "user_id": user_id,
                "text": text[:200],
                "timestamp": now.isoformat(),
                "ttl": ttl_for_injection_log(),
            }
        )
