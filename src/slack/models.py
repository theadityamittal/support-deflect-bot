# src/slack/models.py
"""Frozen dataclass models for Slack events, commands, and SQS messages."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class EventType(Enum):
    MESSAGE = "message"
    APP_MENTION = "app_mention"
    TEAM_JOIN = "team_join"
    COMMAND = "command"


@dataclass(frozen=True)
class SlackEvent:
    """Parsed Slack event — immutable."""

    event_id: str
    workspace_id: str
    user_id: str
    channel_id: str
    text: str
    event_type: EventType
    timestamp: str
    is_bot: bool = False
    thread_ts: str | None = None
    subtype: str | None = None

    @classmethod
    def from_event_body(cls, body: dict[str, Any]) -> SlackEvent:
        """Parse a Slack Events API body into a SlackEvent."""
        event = body.get("event", {})
        event_type_str = event.get("type", "message")

        # team_join wraps user info differently
        if event_type_str == "team_join":
            user_info = event.get("user", {})
            user_id = (
                user_info.get("id", "")
                if isinstance(user_info, dict)
                else str(user_info)
            )
            return cls(
                event_id=body.get("event_id", ""),
                workspace_id=body.get("team_id", ""),
                user_id=user_id,
                channel_id="",
                text="",
                event_type=EventType.TEAM_JOIN,
                timestamp=event.get("event_ts", ""),
                is_bot=False,
            )

        return cls(
            event_id=body.get("event_id", ""),
            workspace_id=body.get("team_id", ""),
            user_id=event.get("user", ""),
            channel_id=event.get("channel", ""),
            text=event.get("text", ""),
            event_type=EventType(event_type_str),
            timestamp=event.get("event_ts", ""),
            is_bot=event.get("bot_id") is not None
            or event.get("subtype") == "bot_message",
            thread_ts=event.get("thread_ts"),
            subtype=event.get("subtype"),
        )


@dataclass(frozen=True)
class SlackCommand:
    """Parsed slash command — immutable."""

    command: str
    user_id: str
    workspace_id: str
    channel_id: str
    trigger_id: str
    text: str
    response_url: str

    @classmethod
    def from_command_body(cls, body: dict[str, Any]) -> SlackCommand:
        """Parse a Slack slash command body."""
        return cls(
            command=body.get("command", ""),
            user_id=body.get("user_id", ""),
            workspace_id=body.get("team_id", ""),
            channel_id=body.get("channel_id", ""),
            trigger_id=body.get("trigger_id", ""),
            text=body.get("text", ""),
            response_url=body.get("response_url", ""),
        )


@dataclass(frozen=True)
class SQSMessage:
    """Normalized message for SQS FIFO queue — immutable."""

    version: str
    event_id: str
    workspace_id: str
    user_id: str
    channel_id: str
    event_type: EventType
    text: str
    timestamp: str
    is_dm: bool = False
    thread_ts: str | None = None
    command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for SQS message body."""
        return {
            "version": self.version,
            "event_id": self.event_id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "event_type": self.event_type.value,
            "text": self.text,
            "timestamp": self.timestamp,
            "metadata": {
                "is_dm": self.is_dm,
                "command": self.command,
                "thread_ts": self.thread_ts,
            },
        }

    @classmethod
    def from_sqs_record(cls, record: dict[str, Any]) -> SQSMessage:
        """Deserialize from an SQS record body."""
        data = json.loads(record["body"])
        metadata = data.get("metadata", {})
        return cls(
            version=data["version"],
            event_id=data["event_id"],
            workspace_id=data["workspace_id"],
            user_id=data["user_id"],
            channel_id=data["channel_id"],
            event_type=EventType(data["event_type"]),
            text=data["text"],
            timestamp=data["timestamp"],
            is_dm=metadata.get("is_dm", False),
            thread_ts=metadata.get("thread_ts"),
            command=metadata.get("command"),
        )


@dataclass(frozen=True)
class MiddlewareResult:
    """Result from a middleware check — immutable."""

    allowed: bool
    reason: str | None = None
    should_respond: bool = True

    @classmethod
    def allow(cls) -> MiddlewareResult:
        return cls(allowed=True)

    @classmethod
    def reject(cls, reason: str) -> MiddlewareResult:
        return cls(allowed=False, reason=reason, should_respond=True)

    @classmethod
    def drop(cls) -> MiddlewareResult:
        return cls(allowed=False, reason=None, should_respond=False)
