"""DynamoDB CRUD operations for single-table design."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from state.models import CompletionRecord, OnboardingPlan, SetupState, WorkspaceConfig
from state.ttl import (
    ttl_for_injection_log,
    ttl_for_lock,
    ttl_for_plan,
    ttl_for_secrets,
    ttl_for_setup,
)

if TYPE_CHECKING:
    from security.crypto import FieldEncryptor

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

    def acquire_lock(
        self, *, workspace_id: str, user_id: str, ttl_seconds: int = 15
    ) -> bool:
        """Acquire a processing lock. Returns True if acquired, False if held.

        Overwrites expired locks immediately (DynamoDB TTL cleanup is lazy).
        """
        try:
            self._table.put_item(
                Item={
                    "pk": f"WORKSPACE#{workspace_id}",
                    "sk": f"LOCK#{user_id}",
                    "ttl": ttl_for_lock(seconds=ttl_seconds),
                },
                ConditionExpression="attribute_not_exists(pk) OR #ttl < :now",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={":now": int(time.time())},
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
        bot_user_id: str,
        bot_token: str | None = None,
        admin_user_id: str = "",
        setup_complete: bool = False,
        website_url: str = "",
        teams: tuple[str, ...] = (),
        channel_mapping: dict[str, Any] | None = None,
        calendar_enabled: bool = False,
    ) -> None:
        """Save workspace configuration (upsert)."""
        item: dict[str, Any] = {
            "pk": f"WORKSPACE#{workspace_id}",
            "sk": "CONFIG",
            "workspace_id": workspace_id,
            "team_name": team_name,
            "bot_user_id": bot_user_id,
            "active": True,
            "admin_user_id": admin_user_id,
            "setup_complete": setup_complete,
            "website_url": website_url,
            "teams": list(teams),
            "channel_mapping": channel_mapping if channel_mapping is not None else {},
            "calendar_enabled": calendar_enabled,
            "updated_at": int(time.time()),
        }
        if bot_token is not None:
            item["bot_token"] = bot_token
        self._table.put_item(Item=item)

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
            bot_token=item.get("bot_token"),
            bot_user_id=item.get("bot_user_id", ""),
            active=item.get("active", True),
            admin_user_id=item.get("admin_user_id", ""),
            setup_complete=item.get("setup_complete", False),
            website_url=item.get("website_url", ""),
            teams=tuple(item.get("teams", [])),
            channel_mapping=dict(item.get("channel_mapping", {})),
            calendar_enabled=item.get("calendar_enabled", False),
        )

    def update_workspace_config(
        self, *, workspace_id: str, updates: dict[str, Any]
    ) -> None:
        """Partial update on the CONFIG record."""
        if not updates:
            return
        expr_parts = []
        attr_names: dict[str, str] = {}
        attr_values: dict[str, Any] = {}
        for key, value in updates.items():
            safe_key = f"#{key}"
            attr_names[safe_key] = key
            attr_values[f":{key}"] = value
            expr_parts.append(f"{safe_key} = :{key}")
        self._table.update_item(
            Key={"pk": f"WORKSPACE#{workspace_id}", "sk": "CONFIG"},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=attr_names,
            ExpressionAttributeValues=attr_values,
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

    def increment_usage(
        self,
        *,
        workspace_id: str,
        user_id: str,
        turns: int = 1,
        output_tokens: int = 0,
        tool_calls: int = 0,
        estimated_cost: float = 0.0,
    ) -> None:
        """Increment daily usage counters atomically."""
        today = date.today().isoformat()
        self._table.update_item(
            Key={
                "pk": f"WORKSPACE#{workspace_id}",
                "sk": f"USAGE#{user_id}#{today}",
            },
            UpdateExpression=(
                "SET turns = if_not_exists(turns, :zero) + :t, "
                "output_tokens = if_not_exists(output_tokens, :zero) + :ot, "
                "tool_calls = if_not_exists(tool_calls, :zero) + :tc, "
                "estimated_cost = if_not_exists(estimated_cost, :fzero) + :ec"
            ),
            ExpressionAttributeValues={
                ":t": turns,
                ":ot": output_tokens,
                ":tc": tool_calls,
                ":ec": Decimal(str(estimated_cost)),
                ":zero": 0,
                ":fzero": Decimal("0"),
            },
        )

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

    def save_workspace_secrets(
        self,
        *,
        workspace_id: str,
        secrets_blob: dict[str, Any],
        encryptor: FieldEncryptor,
    ) -> None:
        """Encrypt secrets_blob as JSON, store in DynamoDB with 90-day TTL."""
        encrypted_data = encryptor.encrypt(json.dumps(secrets_blob))
        self._table.put_item(
            Item={
                "pk": f"WORKSPACE#{workspace_id}",
                "sk": "SECRETS",
                "encrypted_data": encrypted_data,
                "ttl": ttl_for_secrets(),
            }
        )

    def get_workspace_secrets(
        self,
        *,
        workspace_id: str,
        encryptor: FieldEncryptor,
    ) -> dict[str, Any] | None:
        """Retrieve and decrypt workspace secrets. Returns None if not found."""
        response = self._table.get_item(
            Key={"pk": f"WORKSPACE#{workspace_id}", "sk": "SECRETS"}
        )
        item = response.get("Item")
        if not item:
            return None
        plaintext = encryptor.decrypt(item["encrypted_data"])
        result: dict[str, Any] = json.loads(plaintext)
        return result

    def migrate_bot_token_to_secrets(
        self,
        *,
        workspace_id: str,
        encryptor: FieldEncryptor,
    ) -> None:
        """Read bot_token from WorkspaceConfig, encrypt and move to SECRETS, remove from config."""
        config = self.get_workspace_config(workspace_id=workspace_id)
        if not config or not config.bot_token:
            return
        self.save_workspace_secrets(
            workspace_id=workspace_id,
            secrets_blob={"bot_token": config.bot_token},
            encryptor=encryptor,
        )
        self._table.update_item(
            Key={"pk": f"WORKSPACE#{workspace_id}", "sk": "CONFIG"},
            UpdateExpression="REMOVE bot_token",
        )

    def get_bot_token(self, *, workspace_id: str, encryptor: FieldEncryptor) -> str:
        """Get bot_token: SECRETS record first, fallback to WorkspaceConfig + migrate.

        Priority:
        1. DynamoDB SECRETS record (KMS-encrypted) — set during OAuth
        2. WorkspaceConfig plaintext bot_token — legacy; migrated to SECRETS on first read

        Raises:
            ValueError: If no bot_token is found in either location.
        """
        # 1. Try SECRETS record first
        secrets = self.get_workspace_secrets(
            workspace_id=workspace_id, encryptor=encryptor
        )
        if secrets and secrets.get("bot_token"):
            return str(secrets["bot_token"])

        # 2. Fall back to WorkspaceConfig plaintext
        config = self.get_workspace_config(workspace_id=workspace_id)
        if config and config.bot_token:
            # Lazy migration: move token to SECRETS and remove from CONFIG
            self.migrate_bot_token_to_secrets(
                workspace_id=workspace_id, encryptor=encryptor
            )
            return str(config.bot_token)

        msg = f"No bot_token found for workspace {workspace_id}"
        raise ValueError(msg)

    def save_setup_state(self, *, setup_state: SetupState) -> None:
        """Save or update admin setup state with 14-day TTL."""
        self._table.put_item(
            Item={
                "pk": f"WORKSPACE#{setup_state.workspace_id}",
                "sk": "SETUP",
                "workspace_id": setup_state.workspace_id,
                "step": setup_state.step,
                "admin_user_id": setup_state.admin_user_id,
                "website_url": setup_state.website_url,
                "scrape_manifest_key": setup_state.scrape_manifest_key,
                "teams": list(setup_state.teams),
                "channel_mapping": dict(setup_state.channel_mapping),
                "calendar_enabled": setup_state.calendar_enabled,
                "calendar_oauth_initiated": setup_state.calendar_oauth_initiated,
                "created_at": setup_state.created_at,
                "updated_at": setup_state.updated_at,
                "ttl": ttl_for_setup(),
            }
        )

    def get_setup_state(self, *, workspace_id: str) -> SetupState | None:
        """Retrieve admin setup state. Returns None if not found."""
        response = self._table.get_item(
            Key={"pk": f"WORKSPACE#{workspace_id}", "sk": "SETUP"}
        )
        item = response.get("Item")
        if not item:
            return None
        return SetupState(
            step=item["step"],
            admin_user_id=item["admin_user_id"],
            workspace_id=item["workspace_id"],
            website_url=item.get("website_url", ""),
            scrape_manifest_key=item.get("scrape_manifest_key", ""),
            teams=tuple(item.get("teams", [])),
            channel_mapping=dict(item.get("channel_mapping", {})),
            calendar_enabled=item.get("calendar_enabled", False),
            calendar_oauth_initiated=item.get("calendar_oauth_initiated", False),
            created_at=item.get("created_at", ""),
            updated_at=item.get("updated_at", ""),
        )

    def delete_setup_state(self, *, workspace_id: str) -> None:
        """Delete the admin setup state record."""
        self._table.delete_item(Key={"pk": f"WORKSPACE#{workspace_id}", "sk": "SETUP"})

    def complete_setup(
        self, *, workspace_id: str, config_updates: dict[str, Any]
    ) -> None:
        """Update WorkspaceConfig with final values, set setup_complete=True, delete SETUP record."""
        config = self.get_workspace_config(workspace_id=workspace_id)
        if config is None:
            raise ValueError(f"No WorkspaceConfig found for workspace {workspace_id}")

        self.save_workspace_config(
            workspace_id=workspace_id,
            team_name=config.team_name,
            bot_user_id=config.bot_user_id,
            bot_token=config.bot_token,
            setup_complete=True,
            admin_user_id=config_updates.get("admin_user_id", config.admin_user_id),
            website_url=config_updates.get("website_url", config.website_url),
            teams=tuple(config_updates.get("teams", list(config.teams))),
            channel_mapping=config_updates.get(
                "channel_mapping", dict(config.channel_mapping)
            ),
            calendar_enabled=config_updates.get(
                "calendar_enabled", config.calendar_enabled
            ),
        )
        self.delete_setup_state(workspace_id=workspace_id)

    def get_pending_users(self, *, workspace_id: str) -> list[OnboardingPlan]:
        """Query for onboarding plans with status='pending_setup'."""
        from boto3.dynamodb.conditions import Attr
        from boto3.dynamodb.conditions import Key as DynamoKey

        response = self._table.query(
            KeyConditionExpression=(
                DynamoKey("pk").eq(f"WORKSPACE#{workspace_id}")
                & DynamoKey("sk").begins_with("PLAN#")
            ),
            FilterExpression=Attr("status").eq("pending_setup"),
        )
        items = response.get("Items", [])
        return [OnboardingPlan.from_dynamo_item(item) for item in items]
