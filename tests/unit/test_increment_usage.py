"""Tests for increment_usage in DynamoStateStore."""

from __future__ import annotations

from unittest.mock import MagicMock

from state.dynamo import DynamoStateStore


class TestIncrementUsage:
    def test_increments_counters(self):
        mock_table = MagicMock()
        store = DynamoStateStore(table=mock_table)

        store.increment_usage(
            workspace_id="W1",
            user_id="U1",
            turns=1,
            output_tokens=500,
            tool_calls=2,
            estimated_cost=0.01,
        )

        mock_table.update_item.assert_called_once()
        call_kwargs = mock_table.update_item.call_args[1]
        assert call_kwargs["Key"]["pk"] == "WORKSPACE#W1"
        assert "USAGE#U1#" in call_kwargs["Key"]["sk"]
        assert ":t" in call_kwargs["ExpressionAttributeValues"]

    def test_default_zero_increments(self):
        mock_table = MagicMock()
        store = DynamoStateStore(table=mock_table)

        store.increment_usage(workspace_id="W1", user_id="U1")

        mock_table.update_item.assert_called_once()
