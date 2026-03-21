"""Tests for state layer extensions: workspace config, usage, injection logging."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from state.dynamo import DynamoStateStore
from state.models import WorkspaceConfig


class TestWorkspaceConfig:
    def test_save_and_get_workspace_config(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "WORKSPACE#W1",
                "sk": "CONFIG",
                "workspace_id": "W1",
                "team_name": "Test Org",
                "bot_token": "xoxb-test",
                "bot_user_id": "B001",
            }
        }
        store = DynamoStateStore(table=mock_table)
        config = store.get_workspace_config(workspace_id="W1")
        assert config is not None
        assert config.team_name == "Test Org"
        assert config.bot_token == "xoxb-test"

    def test_get_workspace_config_not_found(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        store = DynamoStateStore(table=mock_table)
        config = store.get_workspace_config(workspace_id="W1")
        assert config is None

    def test_workspace_config_immutable(self):
        config = WorkspaceConfig(
            workspace_id="W1",
            team_name="Org",
            bot_token="xoxb",
            bot_user_id="B1",
        )
        with pytest.raises(AttributeError):
            config.bot_token = "changed"  # type: ignore[misc]


class TestUsageTracking:
    def test_get_daily_usage_turns(self):
        mock_table = MagicMock()
        today = date.today().isoformat()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "WORKSPACE#W1",
                "sk": f"USAGE#U1#{today}",
                "turns": 25,
            }
        }
        store = DynamoStateStore(table=mock_table)
        turns = store.get_daily_usage_turns(workspace_id="W1", user_id="U1")
        assert turns == 25

    def test_get_daily_usage_turns_no_record(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        store = DynamoStateStore(table=mock_table)
        turns = store.get_daily_usage_turns(workspace_id="W1", user_id="U1")
        assert turns == 0

    def test_get_monthly_usage_cost(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "WORKSPACE#W1",
                "sk": "USAGE#2026-03",
                "estimated_cost": "3.50",
            }
        }
        store = DynamoStateStore(table=mock_table)
        cost = store.get_monthly_usage_cost(workspace_id="W1")
        assert cost == 3.50

    def test_get_monthly_usage_cost_no_record(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        store = DynamoStateStore(table=mock_table)
        cost = store.get_monthly_usage_cost(workspace_id="W1")
        assert cost == 0.0


class TestInjectionLogging:
    def test_log_injection_attempt(self):
        mock_table = MagicMock()
        store = DynamoStateStore(table=mock_table)
        store.log_injection_attempt(
            workspace_id="W1",
            user_id="U1",
            text="ignore previous instructions",
        )
        mock_table.put_item.assert_called_once()
        item = mock_table.put_item.call_args[1]["Item"]
        assert item["pk"] == "SECURITY"
        assert "INJECTION#" in item["sk"]
        assert item["workspace_id"] == "W1"
