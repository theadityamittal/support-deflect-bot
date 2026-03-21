"""Tests for DynamoDB CRUD operations."""

import json
import time
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from state.dynamo import DynamoStateStore
from state.models import (
    CompletionRecord,
    OnboardingPlan,
    PlanStatus,
    PlanStep,
    SetupState,
    StepStatus,
)


class TestDynamoStateStore:
    def _make_store(self, mock_table=None):
        table = mock_table or MagicMock()
        return DynamoStateStore(table=table)

    def test_get_plan_returns_plan(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "WORKSPACE#W1",
                "sk": "PLAN#U1",
                "workspace_id": "W1",
                "user_id": "U1",
                "user_name": "Jane",
                "role": "events",
                "status": "in_progress",
                "plan": {
                    "version": 1,
                    "steps": [{"id": 1, "title": "Welcome", "status": "pending"}],
                },
                "context": {"key_facts": [], "recent_messages": []},
            }
        }

        store = self._make_store(mock_table)
        plan = store.get_plan(workspace_id="W1", user_id="U1")

        assert plan is not None
        assert plan.workspace_id == "W1"
        assert len(plan.steps) == 1

    def test_get_plan_returns_none_when_missing(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}

        store = self._make_store(mock_table)
        plan = store.get_plan(workspace_id="W1", user_id="U1")
        assert plan is None

    def test_save_plan_puts_item(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)

        plan = OnboardingPlan(
            workspace_id="W1",
            user_id="U1",
            user_name="Jane",
            role="events",
            status=PlanStatus.IN_PROGRESS,
            version=1,
            steps=[PlanStep(id=1, title="Welcome", status=StepStatus.PENDING)],
        )
        store.save_plan(plan)
        mock_table.put_item.assert_called_once()

        item = mock_table.put_item.call_args[1]["Item"]
        assert item["pk"] == "WORKSPACE#W1"
        assert item["sk"] == "PLAN#U1"

    def test_save_plan_includes_ttl(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)

        plan = OnboardingPlan(
            workspace_id="W1",
            user_id="U1",
            user_name="Jane",
            role="events",
            status=PlanStatus.IN_PROGRESS,
            version=1,
            steps=[],
        )
        store.save_plan(plan)

        item = mock_table.put_item.call_args[1]["Item"]
        assert "ttl" in item
        assert isinstance(item["ttl"], int)

    def test_save_completion_record_no_ttl(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)

        record = CompletionRecord(
            workspace_id="W1",
            user_id="U1",
            role="events",
            plan_version=2,
            steps_completed=5,
            replans=1,
            duration_minutes=120,
            channels_assigned=("events",),
            calendar_events_created=0,
        )
        store.save_completion_record(record)

        item = mock_table.put_item.call_args[1]["Item"]
        assert item["pk"] == "WORKSPACE#W1"
        assert item["sk"] == "COMPLETED#U1"
        assert "ttl" not in item

    def test_acquire_lock_succeeds(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)

        result = store.acquire_lock(workspace_id="W1", user_id="U1")
        assert result is True
        mock_table.put_item.assert_called_once()

    def test_acquire_lock_fails_on_condition(self):
        mock_table = MagicMock()
        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}},
            "PutItem",
        )

        store = self._make_store(mock_table)
        result = store.acquire_lock(workspace_id="W1", user_id="U1")
        assert result is False

    def test_release_lock_deletes_item(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)

        store.release_lock(workspace_id="W1", user_id="U1")
        mock_table.delete_item.assert_called_once_with(
            Key={"pk": "WORKSPACE#W1", "sk": "LOCK#U1"}
        )

    def test_get_kill_switch_status(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {"pk": "SYSTEM", "sk": "KILL_SWITCH", "active": True}
        }

        store = self._make_store(mock_table)
        assert store.get_kill_switch_status() is True

    def test_get_kill_switch_default_false(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}

        store = self._make_store(mock_table)
        assert store.get_kill_switch_status() is False

    def test_set_kill_switch_activates(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)

        store.set_kill_switch(active=True)
        mock_table.put_item.assert_called_once()
        item = mock_table.put_item.call_args[1]["Item"]
        assert item["pk"] == "SYSTEM"
        assert item["sk"] == "KILL_SWITCH"
        assert item["active"] is True

    def test_set_kill_switch_deactivates(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)

        store.set_kill_switch(active=False)
        item = mock_table.put_item.call_args[1]["Item"]
        assert item["active"] is False


class TestSecretsRecord:
    def _make_store(self, mock_table=None):
        table = mock_table or MagicMock()
        return DynamoStateStore(table=table)

    def _make_encryptor(self, encrypt_return="ENCRYPTED", decrypt_return=None):
        enc = MagicMock()
        enc.encrypt.return_value = encrypt_return
        if decrypt_return is not None:
            enc.decrypt.return_value = decrypt_return
        return enc

    def test_save_secrets_encrypts_and_stores(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)
        secrets = {"bot_token": "xoxb-123", "google_refresh_token": "1//abc"}
        enc = self._make_encryptor(encrypt_return="ENCRYPTED_BLOB")

        store.save_workspace_secrets(
            workspace_id="W1", secrets_blob=secrets, encryptor=enc
        )

        enc.encrypt.assert_called_once_with(json.dumps(secrets))
        mock_table.put_item.assert_called_once()
        item = mock_table.put_item.call_args[1]["Item"]
        assert item["pk"] == "WORKSPACE#W1"
        assert item["sk"] == "SECRETS"
        assert item["encrypted_data"] == "ENCRYPTED_BLOB"

    def test_save_secrets_updates_ttl(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)
        enc = self._make_encryptor()

        before = int(time.time())
        store.save_workspace_secrets(
            workspace_id="W1", secrets_blob={"bot_token": "xoxb-999"}, encryptor=enc
        )
        after = int(time.time())

        item = mock_table.put_item.call_args[1]["Item"]
        assert "ttl" in item
        expected_min = before + (90 * 86400)
        expected_max = after + (90 * 86400)
        assert expected_min <= item["ttl"] <= expected_max

    def test_get_secrets_decrypts_and_returns(self):
        mock_table = MagicMock()
        secrets = {"bot_token": "xoxb-123", "google_refresh_token": "1//abc"}
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "WORKSPACE#W1",
                "sk": "SECRETS",
                "encrypted_data": "ENCRYPTED_BLOB",
                "ttl": 9999999999,
            }
        }
        store = self._make_store(mock_table)
        enc = self._make_encryptor(decrypt_return=json.dumps(secrets))

        result = store.get_workspace_secrets(workspace_id="W1", encryptor=enc)

        mock_table.get_item.assert_called_once_with(
            Key={"pk": "WORKSPACE#W1", "sk": "SECRETS"}
        )
        enc.decrypt.assert_called_once_with("ENCRYPTED_BLOB")
        assert result == secrets

    def test_get_secrets_nonexistent_returns_none(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        store = self._make_store(mock_table)
        enc = self._make_encryptor()

        result = store.get_workspace_secrets(workspace_id="W1", encryptor=enc)

        assert result is None
        enc.decrypt.assert_not_called()

    def test_lazy_migration_moves_bot_token_from_config(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)
        enc = self._make_encryptor(encrypt_return="ENCRYPTED_TOKEN")

        mock_table.get_item.return_value = {
            "Item": {
                "pk": "WORKSPACE#W1",
                "sk": "CONFIG",
                "workspace_id": "W1",
                "team_name": "Acme",
                "bot_token": "xoxb-original",
                "bot_user_id": "U_BOT",
                "active": True,
                "updated_at": 1000,
            }
        }

        store.migrate_bot_token_to_secrets(workspace_id="W1", encryptor=enc)

        # Should have written to SECRETS
        put_calls = mock_table.put_item.call_args_list
        secrets_calls = [c for c in put_calls if c[1]["Item"].get("sk") == "SECRETS"]
        assert len(secrets_calls) == 1
        secrets_item = secrets_calls[0][1]["Item"]
        assert secrets_item["pk"] == "WORKSPACE#W1"

        # Verify the encrypted data came from the bot_token
        decrypted_secrets = json.loads(enc.encrypt.call_args[0][0])
        assert decrypted_secrets["bot_token"] == "xoxb-original"

        # Should have updated CONFIG to remove bot_token
        update_calls = mock_table.update_item.call_args_list
        assert len(update_calls) == 1
        update_call = update_calls[0][1]
        assert update_call["Key"] == {"pk": "WORKSPACE#W1", "sk": "CONFIG"}
        assert "bot_token" in update_call["UpdateExpression"]


class TestGetBotToken:
    """Tests for DynamoStateStore.get_bot_token unified retrieval with lazy migration."""

    def _make_store(self, mock_table=None):
        table = mock_table or MagicMock()
        return DynamoStateStore(table=table)

    def _make_encryptor(self, encrypt_return="ENCRYPTED", decrypt_return=None):
        enc = MagicMock()
        enc.encrypt.return_value = encrypt_return
        if decrypt_return is not None:
            enc.decrypt.return_value = decrypt_return
        return enc

    def _secrets_item(self, token: str) -> dict:
        return {
            "pk": "WORKSPACE#W1",
            "sk": "SECRETS",
            "encrypted_data": "ENCRYPTED_BLOB",
            "ttl": 9999999999,
        }

    def _config_item(self, token: str | None) -> dict:
        item: dict = {
            "pk": "WORKSPACE#W1",
            "sk": "CONFIG",
            "workspace_id": "W1",
            "team_name": "Acme",
            "bot_user_id": "U_BOT",
            "active": True,
            "updated_at": 1000,
        }
        if token is not None:
            item["bot_token"] = token
        return item

    def test_get_bot_token_reads_from_secrets_first(self):
        """If SECRETS record exists with bot_token, return it without touching CONFIG."""
        mock_table = MagicMock()
        secrets_payload = json.dumps({"bot_token": "xoxb-from-secrets"})
        # get_item always returns SECRETS record
        mock_table.get_item.return_value = {
            "Item": self._secrets_item("xoxb-from-secrets")
        }
        enc = self._make_encryptor(decrypt_return=secrets_payload)

        store = self._make_store(mock_table)
        token = store.get_bot_token(workspace_id="W1", encryptor=enc)

        assert token == "xoxb-from-secrets"
        # CONFIG should NOT be read (only 1 get_item call for SECRETS)
        assert mock_table.get_item.call_count == 1
        enc.decrypt.assert_called_once()

    def test_get_bot_token_falls_back_to_config(self):
        """If SECRETS record has no bot_token, read from WorkspaceConfig plaintext."""
        mock_table = MagicMock()

        def side_effect(**kwargs):
            key = kwargs.get("Key", {})
            if key.get("sk") == "SECRETS":
                return {}  # not found
            return {"Item": self._config_item("xoxb-from-config")}

        mock_table.get_item.side_effect = side_effect
        enc = self._make_encryptor()

        store = self._make_store(mock_table)
        token = store.get_bot_token(workspace_id="W1", encryptor=enc)

        assert token == "xoxb-from-config"
        enc.decrypt.assert_not_called()

    def test_get_bot_token_migrates_from_config_to_secrets(self):
        """When falling back to CONFIG, migrate bot_token to SECRETS and remove from CONFIG."""
        mock_table = MagicMock()

        def side_effect(**kwargs):
            key = kwargs.get("Key", {})
            if key.get("sk") == "SECRETS":
                return {}  # not found
            return {"Item": self._config_item("xoxb-to-migrate")}

        mock_table.get_item.side_effect = side_effect
        enc = self._make_encryptor(encrypt_return="ENCRYPTED_TOKEN")

        store = self._make_store(mock_table)
        token = store.get_bot_token(workspace_id="W1", encryptor=enc)

        assert token == "xoxb-to-migrate"

        # Verify migration: SECRETS record was written
        put_calls = mock_table.put_item.call_args_list
        secrets_puts = [c for c in put_calls if c[1]["Item"].get("sk") == "SECRETS"]
        assert len(secrets_puts) == 1
        assert secrets_puts[0][1]["Item"]["encrypted_data"] == "ENCRYPTED_TOKEN"

        # Verify migration: bot_token removed from CONFIG
        update_calls = mock_table.update_item.call_args_list
        assert len(update_calls) == 1
        update_call = update_calls[0][1]
        assert update_call["Key"] == {"pk": "WORKSPACE#W1", "sk": "CONFIG"}
        assert "bot_token" in update_call["UpdateExpression"]

    def test_get_bot_token_works_when_already_migrated(self):
        """SECRETS record with bot_token — no migration triggered."""
        mock_table = MagicMock()
        secrets_payload = json.dumps({"bot_token": "xoxb-already-migrated"})
        mock_table.get_item.return_value = {
            "Item": self._secrets_item("xoxb-already-migrated")
        }
        enc = self._make_encryptor(decrypt_return=secrets_payload)

        store = self._make_store(mock_table)
        token = store.get_bot_token(workspace_id="W1", encryptor=enc)

        assert token == "xoxb-already-migrated"
        # No migration: no put_item or update_item calls
        mock_table.put_item.assert_not_called()
        mock_table.update_item.assert_not_called()

    def test_get_bot_token_raises_when_not_found(self):
        """Raises ValueError when neither SECRETS nor CONFIG has a bot_token."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}  # all lookups return nothing
        enc = self._make_encryptor()

        store = self._make_store(mock_table)
        with pytest.raises(ValueError, match="No bot_token found"):
            store.get_bot_token(workspace_id="W_MISSING", encryptor=enc)


class TestSetupState:
    def _make_store(self, mock_table=None):
        table = mock_table or MagicMock()
        return DynamoStateStore(table=table)

    def _make_setup_state(self, **overrides):
        defaults = {
            "step": "welcome",
            "admin_user_id": "U_ADMIN",
            "workspace_id": "W1",
            "website_url": "https://example.com",
            "scrape_manifest_key": "",
            "teams": ("eng", "sales"),
            "channel_mapping": {"eng": "C_ENG"},
            "calendar_enabled": False,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        defaults.update(overrides)
        return SetupState(**defaults)

    def test_save_setup_state(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)
        setup = self._make_setup_state()

        store.save_setup_state(setup_state=setup)

        mock_table.put_item.assert_called_once()
        item = mock_table.put_item.call_args[1]["Item"]
        assert item["pk"] == "WORKSPACE#W1"
        assert item["sk"] == "SETUP"
        assert item["step"] == "welcome"
        assert item["admin_user_id"] == "U_ADMIN"
        assert "ttl" in item

    def test_get_setup_state(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "WORKSPACE#W1",
                "sk": "SETUP",
                "step": "awaiting_url",
                "admin_user_id": "U_ADMIN",
                "workspace_id": "W1",
                "website_url": "https://acme.com",
                "scrape_manifest_key": "",
                "teams": ["eng"],
                "channel_mapping": {"eng": "C_ENG"},
                "calendar_enabled": False,
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
            }
        }
        store = self._make_store(mock_table)

        result = store.get_setup_state(workspace_id="W1")

        assert result is not None
        assert isinstance(result, SetupState)
        assert result.step == "awaiting_url"
        assert result.admin_user_id == "U_ADMIN"
        assert result.workspace_id == "W1"
        assert result.teams == ("eng",)
        assert result.channel_mapping == {"eng": "C_ENG"}

    def test_get_setup_state_nonexistent_returns_none(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        store = self._make_store(mock_table)

        result = store.get_setup_state(workspace_id="W1")

        assert result is None

    def test_delete_setup_state(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)

        store.delete_setup_state(workspace_id="W1")

        mock_table.delete_item.assert_called_once_with(
            Key={"pk": "WORKSPACE#W1", "sk": "SETUP"}
        )

    def test_setup_state_has_14_day_ttl(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)
        setup = self._make_setup_state()

        before = int(time.time())
        store.save_setup_state(setup_state=setup)
        after = int(time.time())

        item = mock_table.put_item.call_args[1]["Item"]
        expected_min = before + (14 * 86400)
        expected_max = after + (14 * 86400)
        assert expected_min <= item["ttl"] <= expected_max


class TestPendingUsers:
    def _make_store(self, mock_table=None):
        table = mock_table or MagicMock()
        return DynamoStateStore(table=table)

    def _make_plan_item(self, user_id: str, status: str) -> dict:
        return {
            "pk": "WORKSPACE#W1",
            "sk": f"PLAN#{user_id}",
            "workspace_id": "W1",
            "user_id": user_id,
            "user_name": "Test User",
            "role": "member",
            "status": status,
            "plan": {"version": 1, "steps": []},
            "context": {"key_facts": [], "recent_messages": []},
        }

    def test_get_pending_users_returns_matching_plans(self):
        mock_table = MagicMock()
        mock_table.query.return_value = {
            "Items": [
                self._make_plan_item("U1", "pending_setup"),
                self._make_plan_item("U2", "pending_setup"),
            ]
        }
        store = self._make_store(mock_table)

        result = store.get_pending_users(workspace_id="W1")

        assert len(result) == 2
        assert all(isinstance(p, OnboardingPlan) for p in result)
        assert result[0].user_id == "U1"
        assert result[1].user_id == "U2"

    def test_get_pending_users_empty_when_none(self):
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": []}
        store = self._make_store(mock_table)

        result = store.get_pending_users(workspace_id="W1")

        assert result == []


class TestWorkspaceConfigUpdates:
    def _make_store(self, mock_table=None):
        table = mock_table or MagicMock()
        return DynamoStateStore(table=table)

    def test_save_config_with_new_fields(self):
        mock_table = MagicMock()
        store = self._make_store(mock_table)

        store.save_workspace_config(
            workspace_id="W1",
            team_name="Acme",
            bot_token=None,
            bot_user_id="U_BOT",
            admin_user_id="U_ADMIN",
            setup_complete=True,
            website_url="https://acme.com",
            teams=("eng", "sales"),
            channel_mapping={"eng": "C_ENG"},
            calendar_enabled=True,
        )

        mock_table.put_item.assert_called_once()
        item = mock_table.put_item.call_args[1]["Item"]
        assert item["pk"] == "WORKSPACE#W1"
        assert item["sk"] == "CONFIG"
        assert item["admin_user_id"] == "U_ADMIN"
        assert item["setup_complete"] is True
        assert item["website_url"] == "https://acme.com"
        assert item["teams"] == ["eng", "sales"]
        assert item["channel_mapping"] == {"eng": "C_ENG"}
        assert item["calendar_enabled"] is True

    def test_complete_setup_writes_config_deletes_setup(self):
        mock_table = MagicMock()
        # existing CONFIG record
        mock_table.get_item.return_value = {
            "Item": {
                "pk": "WORKSPACE#W1",
                "sk": "CONFIG",
                "workspace_id": "W1",
                "team_name": "Acme",
                "bot_token": None,
                "bot_user_id": "U_BOT",
                "active": True,
                "admin_user_id": "",
                "setup_complete": False,
                "website_url": "",
                "teams": [],
                "channel_mapping": {},
                "calendar_enabled": False,
                "updated_at": 1000,
            }
        }
        store = self._make_store(mock_table)

        store.complete_setup(
            workspace_id="W1",
            config_updates={
                "admin_user_id": "U_ADMIN",
                "website_url": "https://acme.com",
                "teams": ["eng"],
                "channel_mapping": {"eng": "C_ENG"},
                "calendar_enabled": False,
            },
        )

        # CONFIG should be updated (put_item called)
        put_calls = mock_table.put_item.call_args_list
        config_puts = [c for c in put_calls if c[1]["Item"].get("sk") == "CONFIG"]
        assert len(config_puts) == 1
        config_item = config_puts[0][1]["Item"]
        assert config_item["setup_complete"] is True
        assert config_item["admin_user_id"] == "U_ADMIN"

        # SETUP record should be deleted
        mock_table.delete_item.assert_called_once_with(
            Key={"pk": "WORKSPACE#W1", "sk": "SETUP"}
        )
