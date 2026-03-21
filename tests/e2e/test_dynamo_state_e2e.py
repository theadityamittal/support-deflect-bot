"""E2E tests: DynamoDB state operations against deployed table.

Tests lock lifecycle, workspace config, usage tracking, and kill switch
against the real sherpa DynamoDB table.

Run: .venv/bin/pytest tests/e2e/test_dynamo_state_e2e.py -v -m e2e --no-cov -s
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

import pytest
from security.crypto import FieldEncryptor
from state.dynamo import DynamoStateStore
from state.models import SetupState

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


@pytest.mark.e2e
class TestSecretsE2E:
    """SECRETS record CRUD with real KMS encryption."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, dynamodb_table):
        yield
        with contextlib.suppress(Exception):
            dynamodb_table.delete_item(
                Key={"pk": f"WORKSPACE#{E2E_WORKSPACE_ID}", "sk": "SECRETS"}
            )

    def test_secrets_encrypt_store_retrieve(self, state_store, kms_key_id):
        encryptor = FieldEncryptor(kms_key_id=kms_key_id)
        original = {"bot_token": "xoxb-e2e-secret", "google_refresh_token": "1//e2e"}

        state_store.save_workspace_secrets(
            workspace_id=E2E_WORKSPACE_ID,
            secrets_blob=original,
            encryptor=encryptor,
        )

        retrieved = state_store.get_workspace_secrets(
            workspace_id=E2E_WORKSPACE_ID,
            encryptor=encryptor,
        )

        assert retrieved is not None
        assert retrieved["bot_token"] == "xoxb-e2e-secret"
        assert retrieved["google_refresh_token"] == "1//e2e"
        print(f"  Secrets roundtrip OK: {list(retrieved.keys())}")

    def test_secrets_overwrite(self, state_store, kms_key_id):
        encryptor = FieldEncryptor(kms_key_id=kms_key_id)

        state_store.save_workspace_secrets(
            workspace_id=E2E_WORKSPACE_ID,
            secrets_blob={"bot_token": "old-token"},
            encryptor=encryptor,
        )
        state_store.save_workspace_secrets(
            workspace_id=E2E_WORKSPACE_ID,
            secrets_blob={"bot_token": "new-token"},
            encryptor=encryptor,
        )

        retrieved = state_store.get_workspace_secrets(
            workspace_id=E2E_WORKSPACE_ID,
            encryptor=encryptor,
        )
        assert retrieved is not None
        assert retrieved["bot_token"] == "new-token"
        print("  Secrets overwrite: second write wins")


@pytest.mark.e2e
class TestSetupStateE2E:
    """Setup state lifecycle against real DynamoDB."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, dynamodb_table):
        yield
        with contextlib.suppress(Exception):
            dynamodb_table.delete_item(
                Key={"pk": f"WORKSPACE#{E2E_WORKSPACE_ID}", "sk": "SETUP"}
            )
            dynamodb_table.delete_item(
                Key={"pk": f"WORKSPACE#{E2E_WORKSPACE_ID}", "sk": "CONFIG"}
            )

    def test_setup_state_lifecycle(self, state_store):
        now = datetime.now(UTC).isoformat()
        setup = SetupState(
            step="welcome",
            admin_user_id="U_E2E_ADMIN",
            workspace_id=E2E_WORKSPACE_ID,
            website_url="",
            scrape_manifest_key="",
            teams=(),
            channel_mapping={},
            calendar_enabled=False,
            created_at=now,
            updated_at=now,
        )

        state_store.save_setup_state(setup_state=setup)

        retrieved = state_store.get_setup_state(workspace_id=E2E_WORKSPACE_ID)
        assert retrieved is not None
        assert retrieved.step == "welcome"
        assert retrieved.admin_user_id == "U_E2E_ADMIN"
        print(f"  Setup state saved: step={retrieved.step}")

        # Update step
        updated = SetupState(
            step="teams",
            admin_user_id=retrieved.admin_user_id,
            workspace_id=retrieved.workspace_id,
            website_url="https://example.com",
            scrape_manifest_key="",
            teams=("Engineering", "Design"),
            channel_mapping={},
            calendar_enabled=False,
            created_at=retrieved.created_at,
            updated_at=datetime.now(UTC).isoformat(),
        )
        state_store.save_setup_state(setup_state=updated)

        retrieved2 = state_store.get_setup_state(workspace_id=E2E_WORKSPACE_ID)
        assert retrieved2 is not None
        assert retrieved2.step == "teams"
        assert "Engineering" in retrieved2.teams
        print(f"  Setup state updated: step={retrieved2.step}")

        # Delete
        state_store.delete_setup_state(workspace_id=E2E_WORKSPACE_ID)
        assert state_store.get_setup_state(workspace_id=E2E_WORKSPACE_ID) is None
        print("  Setup state deleted")

    def test_complete_setup_writes_config(self, state_store):
        # Create workspace config with setup_complete=False
        state_store.save_workspace_config(
            workspace_id=E2E_WORKSPACE_ID,
            team_name="E2E Test Team",
            bot_token="xoxb-e2e",
            bot_user_id="U_E2E_BOT",
        )

        # Create setup state
        now = datetime.now(UTC).isoformat()
        setup = SetupState(
            step="confirmation",
            admin_user_id="U_E2E_ADMIN",
            workspace_id=E2E_WORKSPACE_ID,
            website_url="https://example.com",
            scrape_manifest_key="",
            teams=("Engineering",),
            channel_mapping={"Engineering": "C_ENG"},
            calendar_enabled=False,
            created_at=now,
            updated_at=now,
        )
        state_store.save_setup_state(setup_state=setup)

        # Complete setup
        state_store.complete_setup(
            workspace_id=E2E_WORKSPACE_ID,
            config_updates={
                "teams": ["Engineering"],
                "channel_mapping": {"Engineering": "C_ENG"},
                "calendar_enabled": False,
            },
        )

        # Verify config updated
        config = state_store.get_workspace_config(workspace_id=E2E_WORKSPACE_ID)
        assert config is not None
        assert config.setup_complete is True
        assert "Engineering" in config.teams
        print(f"  complete_setup: setup_complete={config.setup_complete}")

        # Verify setup state deleted
        assert state_store.get_setup_state(workspace_id=E2E_WORKSPACE_ID) is None
        print("  Setup state cleaned up after complete_setup")
