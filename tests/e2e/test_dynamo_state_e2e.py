"""E2E tests: DynamoDB state operations against deployed table.

Tests lock lifecycle, workspace config, usage tracking, and kill switch
against the real onboard-assist DynamoDB table.

Run: .venv/bin/pytest tests/e2e/test_dynamo_state_e2e.py -v -m e2e --no-cov -s
"""

from __future__ import annotations

import contextlib

import pytest
from state.dynamo import DynamoStateStore

from tests.e2e.conftest import E2E_WORKSPACE_ID


@pytest.fixture()
def state_store(dynamodb_table):
    return DynamoStateStore(table=dynamodb_table)


@pytest.fixture(autouse=True)
def _cleanup_e2e_records(dynamodb_table):
    """Clean up all E2E test records after each test."""
    yield
    pk = f"WORKSPACE#{E2E_WORKSPACE_ID}"
    with contextlib.suppress(Exception):
        response = dynamodb_table.query(
            KeyConditionExpression="pk = :pk",
            ExpressionAttributeValues={":pk": pk},
        )
        for item in response.get("Items", []):
            dynamodb_table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})


@pytest.mark.e2e
class TestLockLifecycleE2E:
    """Lock acquire/release against real DynamoDB."""

    def test_acquire_and_release(self, state_store):
        user = "U_E2E_LOCK_1"
        acquired = state_store.acquire_lock(workspace_id=E2E_WORKSPACE_ID, user_id=user)
        assert acquired is True, "First acquire should succeed"

        # Second acquire should fail (lock held)
        acquired2 = state_store.acquire_lock(
            workspace_id=E2E_WORKSPACE_ID, user_id=user
        )
        assert acquired2 is False, "Second acquire should fail while lock held"

        # Release
        state_store.release_lock(workspace_id=E2E_WORKSPACE_ID, user_id=user)

        # Re-acquire should succeed
        acquired3 = state_store.acquire_lock(
            workspace_id=E2E_WORKSPACE_ID, user_id=user
        )
        assert acquired3 is True, "Acquire after release should succeed"

        # Cleanup
        state_store.release_lock(workspace_id=E2E_WORKSPACE_ID, user_id=user)
        print("  Lock acquire → block → release → re-acquire: OK")

    def test_different_users_independent_locks(self, state_store):
        user_a = "U_E2E_LOCK_A"
        user_b = "U_E2E_LOCK_B"

        a1 = state_store.acquire_lock(workspace_id=E2E_WORKSPACE_ID, user_id=user_a)
        b1 = state_store.acquire_lock(workspace_id=E2E_WORKSPACE_ID, user_id=user_b)

        assert a1 is True
        assert b1 is True, "Different users should have independent locks"

        state_store.release_lock(workspace_id=E2E_WORKSPACE_ID, user_id=user_a)
        state_store.release_lock(workspace_id=E2E_WORKSPACE_ID, user_id=user_b)
        print("  Independent user locks: OK")


@pytest.mark.e2e
class TestWorkspaceConfigE2E:
    """Workspace config CRUD against real DynamoDB."""

    def test_save_and_get_config(self, state_store):
        state_store.save_workspace_config(
            workspace_id=E2E_WORKSPACE_ID,
            team_name="E2E Test Team",
            bot_token="xoxb-e2e-test-token",
            bot_user_id="U_E2E_BOT",
        )

        config = state_store.get_workspace_config(workspace_id=E2E_WORKSPACE_ID)
        assert config is not None
        assert config.team_name == "E2E Test Team"
        assert config.bot_token == "xoxb-e2e-test-token"
        assert config.bot_user_id == "U_E2E_BOT"
        assert config.active is True
        print(f"  Workspace config saved and retrieved: {config.team_name}")

    def test_config_upsert(self, state_store):
        state_store.save_workspace_config(
            workspace_id=E2E_WORKSPACE_ID,
            team_name="Original Team",
            bot_token="xoxb-original",
            bot_user_id="U_BOT_1",
        )

        # Update with new token
        state_store.save_workspace_config(
            workspace_id=E2E_WORKSPACE_ID,
            team_name="Original Team",
            bot_token="xoxb-rotated",
            bot_user_id="U_BOT_1",
        )

        config = state_store.get_workspace_config(workspace_id=E2E_WORKSPACE_ID)
        assert config is not None
        assert config.bot_token == "xoxb-rotated"
        print("  Config upsert (token rotation): OK")

    def test_nonexistent_workspace_returns_none(self, state_store):
        config = state_store.get_workspace_config(
            workspace_id="E2E_NONEXISTENT_WS_99999"
        )
        assert config is None
        print("  Nonexistent workspace returns None: OK")


@pytest.mark.e2e
class TestUsageTrackingE2E:
    """Usage counters against real DynamoDB."""

    def test_increment_and_read_daily_turns(self, state_store):
        user = "U_E2E_USAGE"

        initial = state_store.get_daily_usage_turns(
            workspace_id=E2E_WORKSPACE_ID, user_id=user
        )

        state_store.increment_usage(
            workspace_id=E2E_WORKSPACE_ID,
            user_id=user,
            turns=3,
            output_tokens=500,
            tool_calls=2,
            estimated_cost=0.01,
        )

        updated = state_store.get_daily_usage_turns(
            workspace_id=E2E_WORKSPACE_ID, user_id=user
        )
        assert updated == initial + 3
        print(f"  Daily turns: {initial} → {updated}")

    def test_increment_is_atomic(self, state_store):
        """Multiple increments should accumulate correctly."""
        user = "U_E2E_ATOMIC"

        state_store.increment_usage(
            workspace_id=E2E_WORKSPACE_ID, user_id=user, turns=1
        )
        state_store.increment_usage(
            workspace_id=E2E_WORKSPACE_ID, user_id=user, turns=1
        )
        state_store.increment_usage(
            workspace_id=E2E_WORKSPACE_ID, user_id=user, turns=1
        )

        total = state_store.get_daily_usage_turns(
            workspace_id=E2E_WORKSPACE_ID, user_id=user
        )
        assert total >= 3, f"Expected at least 3 turns, got {total}"
        print(f"  Atomic increments: {total} turns")


@pytest.mark.e2e
class TestKillSwitchE2E:
    """Kill switch against real DynamoDB."""

    @pytest.fixture(autouse=True)
    def _cleanup_kill_switch(self, dynamodb_table):
        yield
        # Always deactivate after test
        with contextlib.suppress(Exception):
            dynamodb_table.delete_item(Key={"pk": "SYSTEM", "sk": "KILL_SWITCH"})

    def test_kill_switch_lifecycle(self, state_store):
        # Initially off
        assert state_store.get_kill_switch_status() is False

        # Activate
        state_store.set_kill_switch(active=True)
        assert state_store.get_kill_switch_status() is True

        # Deactivate
        state_store.set_kill_switch(active=False)
        assert state_store.get_kill_switch_status() is False
        print("  Kill switch lifecycle: off → on → off: OK")
