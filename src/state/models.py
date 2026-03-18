"""Frozen dataclass models for DynamoDB single-table design.

All models are immutable. To update, create a new instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class StepStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class PlanStatus(Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class PlanStep:
    """A single step in an onboarding plan."""

    id: int
    title: str
    status: StepStatus
    summary: str | None = None
    completed_at: datetime | None = None
    started_at: datetime | None = None
    requires_tool: str | None = None
    channels: tuple[str, ...] = ()


@dataclass(frozen=True)
class OnboardingPlan:
    """Active onboarding plan stored in DynamoDB."""

    workspace_id: str
    user_id: str
    user_name: str
    role: str
    status: PlanStatus
    version: int
    steps: list[PlanStep]
    key_facts: tuple[str, ...] = ()
    recent_messages: tuple[dict[str, Any], ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dynamo_item(self) -> dict[str, Any]:
        """Serialize to a DynamoDB item dict."""
        return {
            "pk": f"WORKSPACE#{self.workspace_id}",
            "sk": f"PLAN#{self.user_id}",
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "role": self.role,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "plan": {
                "version": self.version,
                "steps": [
                    {
                        "id": s.id,
                        "title": s.title,
                        "status": s.status.value,
                        **({"summary": s.summary} if s.summary else {}),
                        **(
                            {"completed_at": s.completed_at.isoformat()}
                            if s.completed_at
                            else {}
                        ),
                        **(
                            {"started_at": s.started_at.isoformat()}
                            if s.started_at
                            else {}
                        ),
                        **(
                            {"requires_tool": s.requires_tool}
                            if s.requires_tool
                            else {}
                        ),
                        **({"channels": list(s.channels)} if s.channels else {}),
                    }
                    for s in self.steps
                ],
            },
            "context": {
                "key_facts": list(self.key_facts),
                "recent_messages": list(self.recent_messages),
            },
        }

    @classmethod
    def from_dynamo_item(cls, item: dict[str, Any]) -> OnboardingPlan:
        """Deserialize from a DynamoDB item dict."""
        plan_data = item.get("plan", {})
        context = item.get("context", {})

        steps = [
            PlanStep(
                id=s["id"],
                title=s["title"],
                status=StepStatus(s["status"]),
                summary=s.get("summary"),
                completed_at=(
                    datetime.fromisoformat(s["completed_at"])
                    if s.get("completed_at")
                    else None
                ),
                started_at=(
                    datetime.fromisoformat(s["started_at"])
                    if s.get("started_at")
                    else None
                ),
                requires_tool=s.get("requires_tool"),
                channels=tuple(s.get("channels", [])),
            )
            for s in plan_data.get("steps", [])
        ]

        return cls(
            workspace_id=item["workspace_id"],
            user_id=item["user_id"],
            user_name=item.get("user_name", ""),
            role=item.get("role", ""),
            status=PlanStatus(item.get("status", "in_progress")),
            version=plan_data.get("version", 1),
            steps=steps,
            key_facts=tuple(context.get("key_facts", [])),
            recent_messages=tuple(context.get("recent_messages", [])),
        )


@dataclass(frozen=True)
class CompletionRecord:
    """Permanent completion record (no TTL)."""

    workspace_id: str
    user_id: str
    role: str
    plan_version: int
    steps_completed: int
    replans: int
    duration_minutes: int
    channels_assigned: tuple[str, ...]
    calendar_events_created: int
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dynamo_item(self) -> dict[str, Any]:
        """Serialize to a DynamoDB item dict. No TTL field."""
        return {
            "pk": f"WORKSPACE#{self.workspace_id}",
            "sk": f"COMPLETED#{self.user_id}",
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "completed_at": self.completed_at.isoformat(),
            "role": self.role,
            "plan_version": self.plan_version,
            "steps_completed": self.steps_completed,
            "replans": self.replans,
            "duration_minutes": self.duration_minutes,
            "channels_assigned": list(self.channels_assigned),
            "calendar_events_created": self.calendar_events_created,
        }


@dataclass(frozen=True)
class UsageRecord:
    """Per-user daily usage tracking."""

    workspace_id: str
    user_id: str
    date: str  # YYYY-MM-DD
    turns: int
    output_tokens: int
    tool_calls: int
    estimated_cost: float


@dataclass(frozen=True)
class WorkspaceConfig:
    """Workspace configuration stored in DynamoDB."""

    workspace_id: str
    team_name: str
    bot_token: str
    bot_user_id: str
    active: bool = True
