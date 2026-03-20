"""Integration tests: admin setup flow from welcome through confirmation.

Tests the full state machine progression from the initial SETUP record
created during Slack OAuth through all 7 steps to WorkspaceConfig written
and pending users enqueued.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from admin.setup import SetupDependencies, process_setup_message
from state.models import (
    OnboardingPlan,
    PlanStatus,
    PlanStep,
    SetupState,
    StepStatus,
    WorkspaceConfig,
)


def _make_setup_state(**kwargs) -> SetupState:
    defaults = {
        "step": "welcome",
        "admin_user_id": "U_ADMIN",
        "workspace_id": "W_TEST",
        "website_url": "",
        "teams": (),
        "channel_mapping": {},
        "calendar_enabled": False,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    defaults.update(kwargs)
    return SetupState(**defaults)


def _make_deps(**kwargs) -> SetupDependencies:
    """Build a SetupDependencies with mocked externals."""
    mock_store = kwargs.pop("state_store", MagicMock())
    mock_slack = kwargs.pop("slack_client", MagicMock())
    mock_encryptor = kwargs.pop("encryptor", MagicMock())
    mock_sqs = kwargs.pop("sqs_client", MagicMock())
    mock_lambda_ctx = kwargs.pop("lambda_context", None)

    return SetupDependencies(
        state_store=mock_store,
        slack_client=mock_slack,
        encryptor=mock_encryptor,
        sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/queue.fifo",
        google_client_id="gclient123",
        google_oauth_redirect_uri="https://app.example.com/google/oauth/callback",
        lambda_context=mock_lambda_ctx,
        sqs_client=mock_sqs,
    )


@pytest.mark.integration
class TestSlackOAuthCreatesSetupRecord:
    """Test that the Slack OAuth callback handler creates the initial SETUP record."""

    def test_oauth_callback_creates_setup_state_record(self):
        """Successful Slack OAuth should call save_setup_state with step='welcome'."""
        from slack import oauth as slack_oauth

        token_response = {
            "ok": True,
            "access_token": "xoxb-test-bot-token",
            "bot_user_id": "B_BOT",
            "authed_user": {"id": "U_ADMIN"},
            "team": {"id": "W_NEW", "name": "New Corp"},
        }
        mock_web_client = MagicMock()
        mock_web_client.oauth_v2_access.return_value = token_response

        mock_table = MagicMock()
        # DynamoDB Table.put_item is used by save_workspace_config/save_workspace_secrets/save_setup_state
        mock_table.put_item = MagicMock()

        mock_encryptor = MagicMock()
        mock_encryptor.encrypt.return_value = "encrypted_blob"

        with (
            patch("slack.oauth.WebClient", return_value=mock_web_client),
            patch("slack.oauth.boto3") as mock_boto3,
            patch("slack.oauth.FieldEncryptor", return_value=mock_encryptor),
            patch("slack.oauth.DynamoStateStore") as mock_store_cls,
            patch.dict(
                "os.environ",
                {
                    "DYNAMODB_TABLE_NAME": "onboard-assist",
                    "KMS_KEY_ID": "arn:aws:kms:us-east-1:123:key/test",
                },
            ),
        ):
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_boto3.resource.return_value.Table.return_value = MagicMock()

            event = {"queryStringParameters": {"code": "oauth_code_abc"}}
            response = slack_oauth.lambda_handler(event, context=None)

        assert response["statusCode"] == 200
        assert "successfully" in response["body"]

        # save_setup_state must have been called to create the initial SETUP record
        mock_store.save_setup_state.assert_called_once()
        call_kwargs = mock_store.save_setup_state.call_args.kwargs
        setup_state = call_kwargs["setup_state"]
        assert setup_state.step == "welcome"
        assert setup_state.workspace_id == "W_NEW"
        assert setup_state.admin_user_id == "U_ADMIN"

    def test_oauth_callback_user_denied_does_not_create_setup_record(self):
        """If the user denies Slack OAuth, no SETUP record should be created."""
        from slack import oauth as slack_oauth

        with patch("slack.oauth.DynamoStateStore") as mock_store_cls:
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store

            event = {"queryStringParameters": {"error": "access_denied"}}
            response = slack_oauth.lambda_handler(event, context=None)

        assert response["statusCode"] == 200
        assert "cancelled" in response["body"].lower()
        mock_store.save_setup_state.assert_not_called()


@pytest.mark.integration
class TestAdminSetupWelcomeStep:
    def test_welcome_sends_greeting_and_advances_to_awaiting_url(self):
        """Welcome step should DM the admin and transition state to awaiting_url."""
        state = _make_setup_state(step="welcome")
        mock_store = MagicMock()
        mock_store.get_pending_users.return_value = []
        mock_slack = MagicMock()
        deps = _make_deps(state_store=mock_store, slack_client=mock_slack)

        new_state = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )

        assert new_state.step == "awaiting_url"
        mock_slack.send_message.assert_called_once()
        call_args = mock_slack.send_message.call_args
        assert call_args.kwargs["channel"] == "U_ADMIN"
        assert "Welcome" in call_args.kwargs["text"]
        mock_store.save_setup_state.assert_called_once()
        saved = mock_store.save_setup_state.call_args.kwargs["setup_state"]
        assert saved.step == "awaiting_url"

    def test_unknown_step_returns_unchanged_state(self):
        """An unrecognised step should not crash and should return the same state."""
        state = _make_setup_state(step="nonexistent_step")
        deps = _make_deps()

        returned = process_setup_message(
            text="hello", action_id=None, setup_state=state, deps=deps
        )

        assert returned is state


@pytest.mark.integration
class TestAdminSetupUrlStep:
    def test_valid_url_transitions_to_teams(self):
        """A valid HTTPS URL should be accepted and state should advance past scraping."""
        state = _make_setup_state(step="awaiting_url")
        mock_store = MagicMock()
        mock_store.get_pending_users.return_value = []
        mock_slack = MagicMock()
        mock_slack.list_usergroups.return_value = [{"name": "Engineering"}]
        deps = _make_deps(state_store=mock_store, slack_client=mock_slack)

        # Patch scrape_site so it doesn't make HTTP requests
        with patch(
            "rag.scraper.scrape_site",
            return_value=[{"url": "https://example.com/page1"}],
        ):
            new_state = process_setup_message(
                text="https://example.com",
                action_id=None,
                setup_state=state,
                deps=deps,
            )

        assert new_state.step == "teams"
        assert new_state.website_url == "https://example.com"
        # save_setup_state should be called for scraping + teams transitions
        assert mock_store.save_setup_state.call_count >= 2

    def test_invalid_url_sends_fallback_guidance(self):
        """An invalid URL should trigger fallback guidance and keep step unchanged."""
        state = _make_setup_state(step="awaiting_url")
        mock_slack = MagicMock()
        deps = _make_deps(slack_client=mock_slack)

        new_state = process_setup_message(
            text="not-a-url",
            action_id=None,
            setup_state=state,
            deps=deps,
        )

        assert new_state.step == "awaiting_url"
        mock_slack.send_message.assert_called_once()

    def test_url_with_path_is_accepted(self):
        """URLs with paths should also be accepted."""
        state = _make_setup_state(step="awaiting_url")
        mock_store = MagicMock()
        mock_store.get_pending_users.return_value = []
        mock_slack = MagicMock()
        mock_slack.list_usergroups.return_value = []
        deps = _make_deps(state_store=mock_store, slack_client=mock_slack)

        with patch("rag.scraper.scrape_site", return_value=[]):
            new_state = process_setup_message(
                text="https://example.org/about/team",
                action_id=None,
                setup_state=state,
                deps=deps,
            )

        assert new_state.website_url == "https://example.org/about/team"

    def test_scraping_failure_falls_through_to_teams(self):
        """If scraping raises an exception, the flow should still proceed to teams."""
        state = _make_setup_state(step="awaiting_url")
        mock_store = MagicMock()
        mock_store.get_pending_users.return_value = []
        mock_slack = MagicMock()
        mock_slack.list_usergroups.return_value = [{"name": "Marketing"}]
        deps = _make_deps(state_store=mock_store, slack_client=mock_slack)

        with patch(
            "rag.scraper.scrape_site", side_effect=RuntimeError("Network error")
        ):
            new_state = process_setup_message(
                text="https://example.com",
                action_id=None,
                setup_state=state,
                deps=deps,
            )

        assert new_state.step == "teams"


@pytest.mark.integration
class TestAdminSetupTeamsStep:
    def test_teams_confirm_action_transitions_to_channels(self):
        """Confirming auto-detected teams should advance to channels step."""
        state = _make_setup_state(
            step="teams",
            website_url="https://example.com",
            teams=("Engineering", "Marketing"),
        )
        mock_store = MagicMock()
        mock_store.get_pending_users.return_value = []
        mock_slack = MagicMock()
        mock_slack.list_channels.return_value = [
            {"id": "C001", "name": "engineering"},
            {"id": "C002", "name": "marketing"},
        ]
        deps = _make_deps(state_store=mock_store, slack_client=mock_slack)

        new_state = process_setup_message(
            text="", action_id="teams_confirm", setup_state=state, deps=deps
        )

        assert new_state.step == "channels"
        mock_slack.list_channels.assert_called_once()

    def test_teams_edit_action_stays_in_teams_step(self):
        """Edit action should prompt for manual input and keep step as teams."""
        state = _make_setup_state(step="teams", teams=("Engineering",))
        mock_slack = MagicMock()
        deps = _make_deps(slack_client=mock_slack)

        new_state = process_setup_message(
            text="", action_id="teams_edit", setup_state=state, deps=deps
        )

        assert new_state.step == "teams"
        mock_slack.send_message.assert_called_once()

    def test_manual_team_input_advances_to_channels(self):
        """Comma-separated team names in text should be accepted and advance the flow."""
        state = _make_setup_state(step="teams", website_url="https://example.com")
        mock_store = MagicMock()
        mock_store.get_pending_users.return_value = []
        mock_slack = MagicMock()
        mock_slack.list_channels.return_value = []
        deps = _make_deps(state_store=mock_store, slack_client=mock_slack)

        new_state = process_setup_message(
            text="Sales, Finance, Operations",
            action_id=None,
            setup_state=state,
            deps=deps,
        )

        assert new_state.step == "channels"
        assert "Sales" in new_state.teams
        assert "Finance" in new_state.teams

    def test_empty_team_input_sends_error_message(self):
        """Empty text with no action_id should prompt for team names."""
        state = _make_setup_state(step="teams")
        mock_slack = MagicMock()
        deps = _make_deps(slack_client=mock_slack)

        new_state = process_setup_message(
            text="   ", action_id=None, setup_state=state, deps=deps
        )

        assert new_state.step == "teams"
        mock_slack.send_message.assert_called_once()


@pytest.mark.integration
class TestAdminSetupChannelsStep:
    def test_channel_map_action_saves_mapping_and_advances_when_complete(self):
        """A channel_map action for the last team should trigger transition to calendar."""
        state = _make_setup_state(
            step="channels",
            website_url="https://example.com",
            teams=("Engineering",),
            channel_mapping={},
        )
        mock_store = MagicMock()
        mock_store.get_pending_users.return_value = []
        mock_slack = MagicMock()
        deps = _make_deps(state_store=mock_store, slack_client=mock_slack)

        new_state = process_setup_message(
            text="C_ENG",
            action_id="channel_map_engineering",
            setup_state=state,
            deps=deps,
        )

        # All teams are now mapped — should advance to calendar
        assert new_state.step == "calendar"
        assert new_state.channel_mapping.get("engineering") == "C_ENG"

    def test_partial_channel_mapping_stays_in_channels(self):
        """Mapping one team when two are expected should stay in channels step."""
        state = _make_setup_state(
            step="channels",
            teams=("Engineering", "Marketing"),
            channel_mapping={},
        )
        mock_store = MagicMock()
        mock_store.get_pending_users.return_value = []
        deps = _make_deps(state_store=mock_store)

        new_state = process_setup_message(
            text="C_ENG",
            action_id="channel_map_engineering",
            setup_state=state,
            deps=deps,
        )

        assert new_state.step == "channels"
        assert "engineering" in new_state.channel_mapping

    def test_unrecognised_action_id_in_channels_is_ignored(self):
        """An action_id that doesn't start with channel_map_ should leave state unchanged."""
        state = _make_setup_state(step="channels", teams=("Engineering",))
        deps = _make_deps()

        new_state = process_setup_message(
            text="something", action_id="unknown_action", setup_state=state, deps=deps
        )

        assert new_state.step == "channels"


@pytest.mark.integration
class TestAdminSetupCalendarStep:
    def test_calendar_enable_sends_oauth_url_and_advances_to_done(self):
        """Enabling calendar should build OAuth URL, send it, and complete setup."""
        state = _make_setup_state(
            step="calendar",
            website_url="https://example.com",
            teams=("Engineering",),
            channel_mapping={"engineering": "C_ENG"},
        )
        mock_store = MagicMock()
        # complete_setup needs get_workspace_config to return a config
        mock_store.get_workspace_config.return_value = WorkspaceConfig(
            workspace_id="W_TEST",
            team_name="Test Corp",
            bot_user_id="B001",
        )
        mock_store.get_pending_users.return_value = []
        mock_slack = MagicMock()
        deps = _make_deps(state_store=mock_store, slack_client=mock_slack)

        new_state = process_setup_message(
            text="", action_id="calendar_enable", setup_state=state, deps=deps
        )

        assert new_state.step == "done"
        assert new_state.calendar_enabled is True
        # Should have sent OAuth URL message + completion summary
        assert mock_slack.send_message.call_count >= 2
        # OAuth URL message should contain accounts.google.com
        oauth_msg = mock_slack.send_message.call_args_list[0].kwargs["text"]
        assert "accounts.google.com" in oauth_msg

    def test_calendar_skip_advances_to_done_with_calendar_disabled(self):
        """Skipping calendar should complete setup with calendar_enabled=False."""
        state = _make_setup_state(
            step="calendar",
            website_url="https://example.com",
            teams=("Engineering",),
            channel_mapping={"engineering": "C_ENG"},
        )
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = WorkspaceConfig(
            workspace_id="W_TEST",
            team_name="Test Corp",
            bot_user_id="B001",
        )
        mock_store.get_pending_users.return_value = []
        mock_slack = MagicMock()
        deps = _make_deps(state_store=mock_store, slack_client=mock_slack)

        new_state = process_setup_message(
            text="", action_id="calendar_skip_setup", setup_state=state, deps=deps
        )

        assert new_state.step == "done"
        assert new_state.calendar_enabled is False

    def test_unknown_action_in_calendar_step_keeps_state(self):
        """Unknown action_id in calendar step should not advance the flow."""
        state = _make_setup_state(step="calendar")
        deps = _make_deps()

        new_state = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )

        assert new_state.step == "calendar"


@pytest.mark.integration
class TestAdminSetupConfirmationStep:
    def test_confirmation_writes_workspace_config_and_deletes_setup_record(self):
        """Confirmation step should call complete_setup with all collected data."""
        state = _make_setup_state(
            step="confirmation",
            website_url="https://example.com",
            teams=("Engineering", "Marketing"),
            channel_mapping={"engineering": "C001", "marketing": "C002"},
            calendar_enabled=False,
        )
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = WorkspaceConfig(
            workspace_id="W_TEST",
            team_name="Test Corp",
            bot_user_id="B001",
        )
        mock_store.get_pending_users.return_value = []
        mock_slack = MagicMock()
        deps = _make_deps(state_store=mock_store, slack_client=mock_slack)

        new_state = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )

        assert new_state.step == "done"
        mock_store.complete_setup.assert_called_once()
        complete_call = mock_store.complete_setup.call_args.kwargs
        assert complete_call["workspace_id"] == "W_TEST"
        assert complete_call["config_updates"]["website_url"] == "https://example.com"
        assert complete_call["config_updates"]["calendar_enabled"] is False

    def test_confirmation_sends_summary_message_to_admin(self):
        """Confirmation should DM the admin with a summary of all configured values."""
        state = _make_setup_state(
            step="confirmation",
            website_url="https://example.com",
            teams=("Engineering",),
            channel_mapping={"engineering": "C001"},
            calendar_enabled=True,
        )
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = WorkspaceConfig(
            workspace_id="W_TEST",
            team_name="Test Corp",
            bot_user_id="B001",
        )
        mock_store.get_pending_users.return_value = []
        mock_slack = MagicMock()
        deps = _make_deps(state_store=mock_store, slack_client=mock_slack)

        process_setup_message(text="", action_id=None, setup_state=state, deps=deps)

        mock_slack.send_message.assert_called_once()
        summary_text = mock_slack.send_message.call_args.kwargs["text"]
        assert "Setup Complete" in summary_text
        assert "https://example.com" in summary_text
        assert "Engineering" in summary_text

    def test_confirmation_enqueues_pending_users(self):
        """Pending users discovered before setup should be enqueued via SQS."""
        state = _make_setup_state(
            step="confirmation",
            website_url="https://example.com",
            teams=("Engineering",),
            channel_mapping={"engineering": "C001"},
        )
        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = WorkspaceConfig(
            workspace_id="W_TEST",
            team_name="Test Corp",
            bot_user_id="B001",
        )
        pending_plan = OnboardingPlan(
            workspace_id="W_TEST",
            user_id="U_PENDING",
            user_name="Pending User",
            role="engineering",
            status=PlanStatus.PENDING_SETUP,
            version=1,
            steps=[PlanStep(id=1, title="Welcome", status=StepStatus.PENDING)],
        )
        mock_sqs = MagicMock()
        mock_slack = MagicMock()
        mock_store.get_pending_users.return_value = [pending_plan]
        deps = _make_deps(
            state_store=mock_store, sqs_client=mock_sqs, slack_client=mock_slack
        )

        process_setup_message(text="", action_id=None, setup_state=state, deps=deps)

        mock_sqs.send_message.assert_called_once()
        sqs_body = mock_sqs.send_message.call_args.kwargs["MessageBody"]
        payload = json.loads(sqs_body)
        assert payload["type"] == "onboard_user"
        assert payload["user_id"] == "U_PENDING"


@pytest.mark.integration
class TestAdminSetupFullFlow:
    def test_full_setup_flow_without_calendar(self):
        """Simulate the complete admin setup from welcome through confirmation (no calendar)."""
        workspace_id = "W_FULL"
        admin_id = "U_ADMIN_FULL"

        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = WorkspaceConfig(
            workspace_id=workspace_id,
            team_name="Full Test Corp",
            bot_user_id="B_FULL",
        )
        mock_store.get_pending_users.return_value = []
        mock_slack = MagicMock()
        mock_slack.list_usergroups.return_value = [{"name": "Engineering"}]
        mock_slack.list_channels.return_value = [{"id": "C001", "name": "engineering"}]

        deps = _make_deps(state_store=mock_store, slack_client=mock_slack)

        # Step 1: welcome → awaiting_url
        state = _make_setup_state(
            step="welcome", admin_user_id=admin_id, workspace_id=workspace_id
        )
        state = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )
        assert state.step == "awaiting_url"

        # Step 2: awaiting_url → teams (via scraping)
        with patch("rag.scraper.scrape_site", return_value=[]):
            state = process_setup_message(
                text="https://fulltest.example.com",
                action_id=None,
                setup_state=state,
                deps=deps,
            )
        assert state.step == "teams"
        assert state.website_url == "https://fulltest.example.com"

        # Step 3: teams confirm → channels
        state = process_setup_message(
            text="", action_id="teams_confirm", setup_state=state, deps=deps
        )
        assert state.step == "channels"

        # Step 4: channel_map → calendar (all teams mapped)
        state = process_setup_message(
            text="C001",
            action_id="channel_map_engineering",
            setup_state=state,
            deps=deps,
        )
        assert state.step == "calendar"

        # Step 5: calendar skip → done
        state = process_setup_message(
            text="", action_id="calendar_skip_setup", setup_state=state, deps=deps
        )
        assert state.step == "done"

        # Verify complete_setup was called
        mock_store.complete_setup.assert_called_once()
        final_config = mock_store.complete_setup.call_args.kwargs["config_updates"]
        assert final_config["website_url"] == "https://fulltest.example.com"
        assert final_config["calendar_enabled"] is False

    def test_full_setup_flow_with_calendar_enabled(self):
        """Simulate the complete admin setup with Google Calendar opt-in."""
        workspace_id = "W_CAL"
        admin_id = "U_ADMIN_CAL"

        mock_store = MagicMock()
        mock_store.get_workspace_config.return_value = WorkspaceConfig(
            workspace_id=workspace_id,
            team_name="Calendar Corp",
            bot_user_id="B_CAL",
        )
        mock_store.get_pending_users.return_value = []
        mock_slack = MagicMock()
        mock_slack.list_usergroups.return_value = []
        mock_slack.list_channels.return_value = []

        deps = _make_deps(state_store=mock_store, slack_client=mock_slack)

        # welcome → awaiting_url
        state = _make_setup_state(
            step="welcome", admin_user_id=admin_id, workspace_id=workspace_id
        )
        state = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )

        # awaiting_url → teams (no usergroups detected)
        with patch("rag.scraper.scrape_site", return_value=[]):
            state = process_setup_message(
                text="https://caltest.example.com",
                action_id=None,
                setup_state=state,
                deps=deps,
            )
        assert state.step == "teams"

        # Manual teams → channels
        state = process_setup_message(
            text="Events, Admin",
            action_id=None,
            setup_state=state,
            deps=deps,
        )
        assert state.step == "channels"

        # Map events channel
        state = process_setup_message(
            text="C_EVENTS",
            action_id="channel_map_events",
            setup_state=state,
            deps=deps,
        )
        # Map admin channel — now all teams mapped → calendar
        state = process_setup_message(
            text="C_ADMIN",
            action_id="channel_map_admin",
            setup_state=state,
            deps=deps,
        )
        assert state.step == "calendar"

        # Enable calendar → done
        state = process_setup_message(
            text="", action_id="calendar_enable", setup_state=state, deps=deps
        )
        assert state.step == "done"
        assert state.calendar_enabled is True

        mock_store.complete_setup.assert_called_once()
        final_config = mock_store.complete_setup.call_args.kwargs["config_updates"]
        assert final_config["calendar_enabled"] is True
