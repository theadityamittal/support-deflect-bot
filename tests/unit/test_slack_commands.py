"""Tests for slash command handlers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from slack.commands import handle_command
from slack.models import SlackCommand
from state.models import (
    OnboardingPlan,
    PlanStatus,
    PlanStep,
    SetupState,
    StepStatus,
    WorkspaceConfig,
)


def _make_command(command: str, user_id: str = "U1") -> SlackCommand:
    return SlackCommand(
        command=command,
        user_id=user_id,
        workspace_id="W1",
        channel_id="C1",
        trigger_id="T1",
        text="",
        response_url="https://hooks.slack.com/commands/xxx",
    )


class TestHandleCommand:
    def test_status_with_active_plan(self):
        mock_store = MagicMock()
        mock_store.get_plan.return_value = OnboardingPlan(
            workspace_id="W1",
            user_id="U1",
            user_name="Jane",
            role="events",
            status=PlanStatus.IN_PROGRESS,
            version=1,
            steps=[
                PlanStep(id=1, title="Welcome", status=StepStatus.COMPLETED),
                PlanStep(id=2, title="Intake", status=StepStatus.IN_PROGRESS),
                PlanStep(id=3, title="Training", status=StepStatus.PENDING),
            ],
        )
        response = handle_command(
            _make_command("/sherpa-status"), state_store=mock_store
        )
        assert response["statusCode"] == 200
        body = response["body"]
        assert "Jane" in body or "Progress" in body

    def test_status_with_no_plan(self):
        mock_store = MagicMock()
        mock_store.get_plan.return_value = None
        response = handle_command(
            _make_command("/sherpa-status"), state_store=mock_store
        )
        assert response["statusCode"] == 200
        assert "no active" in response["body"].lower()

    def test_help_returns_static(self):
        mock_store = MagicMock()
        response = handle_command(_make_command("/sherpa-help"), state_store=mock_store)
        assert response["statusCode"] == 200
        assert "/sherpa-status" in response["body"]

    def test_restart_returns_confirmation(self):
        mock_store = MagicMock()
        response = handle_command(
            _make_command("/sherpa-restart"), state_store=mock_store
        )
        assert response["statusCode"] == 200
        assert (
            "confirm" in response["body"].lower()
            or "restart" in response["body"].lower()
        )

    def test_unknown_command(self):
        mock_store = MagicMock()
        response = handle_command(_make_command("/unknown"), state_store=mock_store)
        assert response["statusCode"] == 200
        assert (
            "unknown" in response["body"].lower() or "help" in response["body"].lower()
        )


def _make_config(
    *,
    admin_user_id: str = "ADMIN1",
    setup_complete: bool = False,
    calendar_enabled: bool = False,
) -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_id="W1",
        team_name="Test Team",
        bot_user_id="BOT1",
        admin_user_id=admin_user_id,
        setup_complete=setup_complete,
        calendar_enabled=calendar_enabled,
    )


def _make_setup_state(step: str = "welcome") -> SetupState:
    return SetupState(
        step=step,
        admin_user_id="ADMIN1",
        workspace_id="W1",
    )


class TestOnboardSetup:
    def test_non_admin_rejected(self):
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = _make_config(
            admin_user_id="ADMIN1"
        )
        mock_store.get_setup_state.return_value = None
        response = handle_command(
            _make_command("/sherpa-setup", user_id="OTHER_USER"),
            state_store=mock_store,
        )
        assert response["statusCode"] == 200
        body = response["body"]
        assert "admin" in body.lower()

    def test_setup_in_progress_resumes(self):
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = _make_config(
            admin_user_id="ADMIN1"
        )
        mock_store.get_setup_state.return_value = _make_setup_state(step="awaiting_url")
        response = handle_command(
            _make_command("/sherpa-setup", user_id="ADMIN1"),
            state_store=mock_store,
        )
        assert response["statusCode"] == 200
        body = response["body"]
        assert "awaiting_url" in body
        assert "resuming" in body.lower() or "in progress" in body.lower()

    def test_setup_complete_shows_config(self):
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = _make_config(
            admin_user_id="ADMIN1",
            setup_complete=True,
        )
        mock_store.get_setup_state.return_value = None
        response = handle_command(
            _make_command("/sherpa-setup", user_id="ADMIN1"),
            state_store=mock_store,
        )
        assert response["statusCode"] == 200
        body = response["body"]
        assert "configuration" in body.lower() or "team" in body.lower()

    def test_no_setup_starts_fresh(self):
        mock_store = MagicMock()
        # CONFIG must exist for setup to proceed (OAuth creates it)
        mock_store.get_workspace_config.return_value = _make_config(
            admin_user_id="ADMIN1"
        )
        mock_store.get_setup_state.return_value = None
        response = handle_command(
            _make_command("/sherpa-setup", user_id="ADMIN1"),
            state_store=mock_store,
        )
        assert response["statusCode"] == 200
        body = response["body"]
        assert "starting" in body.lower() or "setup" in body.lower()
        mock_store.save_setup_state.assert_called_once()


class TestOnboardCalendar:
    def test_non_admin_rejected(self):
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = _make_config(
            admin_user_id="ADMIN1",
            setup_complete=True,
        )
        response = handle_command(
            _make_command("/sherpa-calendar", user_id="OTHER_USER"),
            state_store=mock_store,
        )
        assert response["statusCode"] == 200
        body = response["body"]
        assert "admin" in body.lower()

    def test_setup_incomplete_rejected(self):
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = _make_config(
            admin_user_id="ADMIN1",
            setup_complete=False,
        )
        response = handle_command(
            _make_command("/sherpa-calendar", user_id="ADMIN1"),
            state_store=mock_store,
        )
        assert response["statusCode"] == 200
        body = response["body"]
        assert "setup" in body.lower()

    def test_calendar_linked_shows_relink(self):
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = _make_config(
            admin_user_id="ADMIN1",
            setup_complete=True,
            calendar_enabled=True,
        )
        response = handle_command(
            _make_command("/sherpa-calendar", user_id="ADMIN1"),
            state_store=mock_store,
        )
        assert response["statusCode"] == 200
        parsed = json.loads(response["body"])
        assert "blocks" in parsed
        blocks_text = json.dumps(parsed["blocks"])
        assert "relink" in blocks_text.lower() or "calendar_relink" in blocks_text

    def test_calendar_not_linked_shows_enable(self):
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = _make_config(
            admin_user_id="ADMIN1",
            setup_complete=True,
            calendar_enabled=False,
        )
        response = handle_command(
            _make_command("/sherpa-calendar", user_id="ADMIN1"),
            state_store=mock_store,
        )
        assert response["statusCode"] == 200
        parsed = json.loads(response["body"])
        assert "blocks" in parsed
        blocks_text = json.dumps(parsed["blocks"])
        assert "enable" in blocks_text.lower() or "calendar_enable" in blocks_text


class TestAdminGate:
    def test_no_config_creates_config_and_starts_setup(self):
        """CONFIG=None should create minimal CONFIG and start fresh setup."""
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = None
        mock_store.get_setup_state.return_value = None
        response = handle_command(
            _make_command("/sherpa-setup", user_id="U_FIRST"),
            state_store=mock_store,
        )
        body = response["body"]
        assert "starting" in body.lower() or "setup" in body.lower()
        mock_store.save_workspace_config.assert_called_once_with(
            workspace_id="W1",
            team_name="",
            bot_user_id="",
            admin_user_id="U_FIRST",
        )
        mock_store.save_setup_state.assert_called_once()

    def test_empty_admin_claims_admin(self):
        """CONFIG with empty admin_user_id lets first user claim admin."""
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = _make_config(admin_user_id="")
        mock_store.get_setup_state.return_value = None
        mock_store.update_workspace_config = MagicMock()

        response = handle_command(
            _make_command("/sherpa-setup", user_id="U_FIRST"),
            state_store=mock_store,
        )
        body = response["body"]
        assert "starting" in body.lower() or "setup" in body.lower()
        mock_store.update_workspace_config.assert_called_once_with(
            workspace_id="W1",
            updates={"admin_user_id": "U_FIRST"},
        )

    def test_non_admin_during_active_setup_rejected(self):
        """Non-admin should be rejected even when setup is in progress."""
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = _make_config(
            admin_user_id="ADMIN1"
        )
        response = handle_command(
            _make_command("/sherpa-setup", user_id="OTHER_USER"),
            state_store=mock_store,
        )
        body = response["body"]
        assert "admin" in body.lower()
        mock_store.get_setup_state.assert_not_called()


class TestResumeEventId:
    @patch("slack.commands._enqueue_setup_resume")
    def test_resume_event_id_has_timestamp(self, mock_enqueue):
        """Verify that _enqueue_setup_resume is called on resume."""
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = _make_config(
            admin_user_id="ADMIN1"
        )
        mock_store.get_setup_state.return_value = _make_setup_state(step="teams")
        handle_command(
            _make_command("/sherpa-setup", user_id="ADMIN1"),
            state_store=mock_store,
        )
        mock_enqueue.assert_called_once()

    @patch("slack.handler._enqueue_to_sqs")
    def test_event_id_contains_timestamp_ms(self, mock_sqs):
        """The SQS message event_id should contain a timestamp component."""
        from slack.commands import _enqueue_setup_resume

        cmd = _make_command("/sherpa-setup", user_id="ADMIN1")
        _enqueue_setup_resume(cmd)

        msg = mock_sqs.call_args[0][0]
        parts = msg.event_id.split(":")
        assert len(parts) == 4  # setup_resume:workspace:user:timestamp
        assert parts[0] == "setup_resume"
        assert parts[3].isdigit()


class TestUpdateWorkspaceConfig:
    def test_update_workspace_config_partial_update(self):
        from state.dynamo import DynamoStateStore

        mock_table = MagicMock()
        store = DynamoStateStore(table=mock_table)

        store.update_workspace_config(
            workspace_id="W1",
            updates={"admin_user_id": "U_NEW"},
        )

        mock_table.update_item.assert_called_once()
        call_kwargs = mock_table.update_item.call_args[1]
        assert call_kwargs["Key"] == {"pk": "WORKSPACE#W1", "sk": "CONFIG"}
        assert ":admin_user_id" in call_kwargs["ExpressionAttributeValues"]
        assert call_kwargs["ExpressionAttributeValues"][":admin_user_id"] == "U_NEW"
