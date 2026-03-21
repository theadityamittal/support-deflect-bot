# tests/unit/test_slack_models.py
"""Tests for Slack event models."""

from __future__ import annotations

import pytest

from slack.models import (
    EventType,
    MiddlewareResult,
    SlackCommand,
    SlackEvent,
    SQSMessage,
)


class TestSlackEvent:
    def test_from_event_body_message(self):
        body = {
            "event": {
                "type": "message",
                "user": "U123",
                "channel": "C789",
                "text": "Hello bot",
                "ts": "1710769830.000100",
                "event_ts": "1710769830.000100",
            },
            "event_id": "Ev001",
            "team_id": "W456",
        }
        event = SlackEvent.from_event_body(body)
        assert event.event_type == EventType.MESSAGE
        assert event.user_id == "U123"
        assert event.workspace_id == "W456"
        assert event.text == "Hello bot"
        assert event.channel_id == "C789"
        assert event.thread_ts is None

    def test_from_event_body_app_mention(self):
        body = {
            "event": {
                "type": "app_mention",
                "user": "U123",
                "channel": "C789",
                "text": "<@B001> help me",
                "ts": "1710769830.000100",
                "event_ts": "1710769830.000100",
                "thread_ts": "1710769800.000050",
            },
            "event_id": "Ev002",
            "team_id": "W456",
        }
        event = SlackEvent.from_event_body(body)
        assert event.event_type == EventType.APP_MENTION
        assert event.thread_ts == "1710769800.000050"

    def test_from_event_body_team_join(self):
        body = {
            "event": {
                "type": "team_join",
                "user": {"id": "U999", "name": "newuser"},
            },
            "event_id": "Ev003",
            "team_id": "W456",
        }
        event = SlackEvent.from_event_body(body)
        assert event.event_type == EventType.TEAM_JOIN
        assert event.user_id == "U999"
        assert event.text == ""

    def test_subtype_parsed_from_event_body(self):
        body = {
            "event": {
                "type": "message",
                "user": "U123",
                "channel": "C789",
                "text": "Hello",
                "subtype": "channel_join",
                "event_ts": "123",
            },
            "event_id": "Ev004",
            "team_id": "W456",
        }
        event = SlackEvent.from_event_body(body)
        assert event.subtype == "channel_join"

    def test_subtype_defaults_to_none(self):
        body = {
            "event": {
                "type": "message",
                "user": "U123",
                "channel": "C789",
                "text": "Hello",
                "event_ts": "123",
            },
            "event_id": "Ev005",
            "team_id": "W456",
        }
        event = SlackEvent.from_event_body(body)
        assert event.subtype is None

    def test_team_join_subtype_is_none(self):
        body = {
            "event": {
                "type": "team_join",
                "user": {"id": "U999", "name": "newuser"},
            },
            "event_id": "Ev006",
            "team_id": "W456",
        }
        event = SlackEvent.from_event_body(body)
        assert event.subtype is None

    def test_is_bot_message(self):
        event = SlackEvent(
            event_id="Ev001",
            workspace_id="W456",
            user_id="U123",
            channel_id="C789",
            text="hello",
            event_type=EventType.MESSAGE,
            timestamp="1710769830.000100",
            is_bot=True,
        )
        assert event.is_bot is True

    def test_immutable(self):
        event = SlackEvent(
            event_id="Ev001",
            workspace_id="W456",
            user_id="U123",
            channel_id="C789",
            text="hello",
            event_type=EventType.MESSAGE,
            timestamp="1710769830.000100",
        )
        with pytest.raises(AttributeError):
            event.text = "changed"  # type: ignore[misc]


class TestSlackCommand:
    def test_from_command_body(self):
        body = {
            "command": "/sherpa-status",
            "user_id": "U123",
            "team_id": "W456",
            "channel_id": "C789",
            "trigger_id": "T001",
            "text": "",
            "response_url": "https://hooks.slack.com/commands/xxx",
        }
        cmd = SlackCommand.from_command_body(body)
        assert cmd.command == "/sherpa-status"
        assert cmd.user_id == "U123"
        assert cmd.workspace_id == "W456"


class TestSQSMessage:
    def test_to_dict(self):
        msg = SQSMessage(
            version="1.0",
            event_id="Ev001",
            workspace_id="W456",
            user_id="U123",
            channel_id="C789",
            event_type=EventType.MESSAGE,
            text="Hello",
            timestamp="2026-03-18T14:30:00Z",
            is_dm=True,
        )
        d = msg.to_dict()
        assert d["version"] == "1.0"
        assert d["event_type"] == "message"
        assert d["metadata"]["is_dm"] is True

    def test_from_sqs_record(self):
        record = {
            "body": '{"version":"1.0","event_id":"Ev001","workspace_id":"W456","user_id":"U123","channel_id":"C789","event_type":"message","text":"Hello","timestamp":"2026-03-18T14:30:00Z","metadata":{"is_dm":true,"command":null}}'
        }
        msg = SQSMessage.from_sqs_record(record)
        assert msg.event_id == "Ev001"
        assert msg.is_dm is True


class TestMiddlewareResult:
    def test_allow(self):
        result = MiddlewareResult.allow()
        assert result.allowed is True
        assert result.reason is None

    def test_reject(self):
        result = MiddlewareResult.reject("rate limited")
        assert result.allowed is False
        assert result.reason == "rate limited"

    def test_drop(self):
        result = MiddlewareResult.drop()
        assert result.allowed is False
        assert result.should_respond is False
