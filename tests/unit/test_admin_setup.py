"""Tests for admin setup state machine."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from admin.setup import SetupDependencies, _is_valid_url, process_setup_message
from llm.provider import LLMResponse
from state.models import (
    OnboardingPlan,
    PlanStatus,
    PlanStep,
    SetupState,
    StepStatus,
    WorkspaceConfig,
)


def _make_state(
    step: str = "welcome",
    admin_user_id: str = "U_ADMIN",
    workspace_id: str = "W1",
    **kwargs,
) -> SetupState:
    return SetupState(
        step=step,
        admin_user_id=admin_user_id,
        workspace_id=workspace_id,
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
        **kwargs,
    )


def _make_deps(**overrides) -> SetupDependencies:
    defaults = {
        "state_store": MagicMock(),
        "slack_client": MagicMock(),
        "encryptor": MagicMock(),
        "sqs_queue_url": "https://sqs.us-east-1.amazonaws.com/123/queue",
        "google_client_id": "test-client-id",
        "google_oauth_redirect_uri": "https://example.com/callback",
        "lambda_context": MagicMock(),
        "sqs_client": MagicMock(),
        "s3_client": MagicMock(),
        "s3_bucket": "test-bucket",
    }
    defaults.update(overrides)
    # Default: plenty of time remaining
    defaults["lambda_context"].get_remaining_time_in_millis.return_value = 300_000
    return SetupDependencies(**defaults)


class TestAdminSetup:
    def test_welcome_step_sends_greeting(self):
        state = _make_state(step="welcome")
        deps = _make_deps()

        result = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )

        deps.slack_client.send_message.assert_called_once()
        msg = deps.slack_client.send_message.call_args[1]["text"]
        assert "welcome" in msg.lower()
        assert result.step == "awaiting_url"
        deps.state_store.save_setup_state.assert_called()

    def test_awaiting_url_validates_url(self):
        state = _make_state(step="awaiting_url")
        deps = _make_deps()
        # list_usergroups returns empty so we go through teams path
        deps.slack_client.list_usergroups.return_value = []

        with patch("rag.scraper.scrape_site", side_effect=Exception("no network")):
            result = process_setup_message(
                text="https://example.com",
                action_id=None,
                setup_state=state,
                deps=deps,
            )

        assert result.website_url == "https://example.com"
        # Should have progressed past scraping to teams
        assert result.step == "teams"

    def test_awaiting_url_rejects_non_url(self):
        state = _make_state(step="awaiting_url")
        deps = _make_deps()

        result = process_setup_message(
            text="not a url at all",
            action_id=None,
            setup_state=state,
            deps=deps,
        )

        # State should remain unchanged
        assert result.step == "awaiting_url"
        msg = deps.slack_client.send_message.call_args[1]["text"]
        assert "valid url" in msg.lower()

    def test_scraping_sends_progress_updates(self):
        state = _make_state(step="awaiting_url")
        deps = _make_deps()
        deps.slack_client.list_usergroups.return_value = []

        mock_pages = [MagicMock() for _ in range(3)]
        with patch("rag.scraper.scrape_site", return_value=mock_pages):
            process_setup_message(
                text="https://example.com",
                action_id=None,
                setup_state=state,
                deps=deps,
            )

        # Should have sent scraping progress message
        messages = [c[1]["text"] for c in deps.slack_client.send_message.call_args_list]
        assert any("3 pages" in m for m in messages)

    def test_scraping_self_enqueues_on_timeout(self):
        state = _make_state(step="awaiting_url")
        deps = _make_deps()
        # Simulate low remaining time
        deps.lambda_context.get_remaining_time_in_millis.return_value = 30_000

        with patch("rag.scraper.scrape_site"):
            result = process_setup_message(
                text="https://example.com",
                action_id=None,
                setup_state=state,
                deps=deps,
            )

        # Should have saved manifest and enqueued SQS message
        assert result.scrape_manifest_key != ""
        deps.sqs_client.send_message.assert_called()
        sqs_body = json.loads(deps.sqs_client.send_message.call_args[1]["MessageBody"])
        assert sqs_body["type"] == "setup_resume"

    def test_scraping_resumes_from_manifest(self):
        state = _make_state(
            step="scraping",
            website_url="https://example.com",
            scrape_manifest_key="scrape-manifest/W1.json",
        )
        deps = _make_deps()
        deps.slack_client.list_usergroups.return_value = []

        result = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )

        # Should resume and eventually transition to teams
        assert result.step == "teams"
        messages = [c[1]["text"] for c in deps.slack_client.send_message.call_args_list]
        assert any("resum" in m.lower() for m in messages)

    def test_teams_step_auto_detects_usergroups(self):
        state = _make_state(step="awaiting_url")
        deps = _make_deps()
        deps.slack_client.list_usergroups.return_value = [
            {"name": "Engineering", "handle": "engineering"},
            {"name": "Marketing", "handle": "marketing"},
        ]

        with patch("rag.scraper.scrape_site", side_effect=Exception("no network")):
            result = process_setup_message(
                text="https://example.com",
                action_id=None,
                setup_state=state,
                deps=deps,
            )

        assert result.step == "teams"
        assert "Engineering" in result.teams
        assert "Marketing" in result.teams
        # Should have sent team_confirmation blocks
        block_calls = [
            c
            for c in deps.slack_client.send_message.call_args_list
            if c[1].get("blocks") is not None
        ]
        assert len(block_calls) >= 1

    def test_teams_step_fallback_manual_input(self):
        # When no usergroups detected, admin types team names
        state = _make_state(step="teams", teams=())
        deps = _make_deps()
        deps.slack_client.list_channels.return_value = [
            {"id": "C1", "name": "general"},
        ]

        result = process_setup_message(
            text="Engineering, Marketing, Sales",
            action_id=None,
            setup_state=state,
            deps=deps,
        )

        assert "Engineering" in result.teams
        assert "Marketing" in result.teams
        assert "Sales" in result.teams
        assert result.step == "channels"

    def test_channels_step_fetches_conversations(self):
        state = _make_state(
            step="teams",
            teams=("Engineering",),
        )
        deps = _make_deps()
        deps.slack_client.list_channels.return_value = [
            {"id": "C1", "name": "general"},
            {"id": "C2", "name": "engineering"},
        ]

        # Confirm teams to transition to channels
        result = process_setup_message(
            text="", action_id="teams_confirm", setup_state=state, deps=deps
        )

        assert result.step == "channels"
        deps.slack_client.list_channels.assert_called_once()
        # Should have sent channel_mapping blocks
        block_calls = [
            c
            for c in deps.slack_client.send_message.call_args_list
            if c[1].get("blocks") is not None
        ]
        assert len(block_calls) >= 1

    def test_calendar_enable_sends_oauth_url(self):
        state = _make_state(step="calendar")
        deps = _make_deps()
        deps.state_store.get_pending_users.return_value = []
        deps.state_store.get_workspace_config.return_value = None
        # complete_setup needs a workspace config
        deps.state_store.complete_setup.return_value = None

        result = process_setup_message(
            text="", action_id="calendar_enable", setup_state=state, deps=deps
        )

        messages = [c[1]["text"] for c in deps.slack_client.send_message.call_args_list]
        assert any("accounts.google.com" in m for m in messages)
        assert result.calendar_enabled is False
        assert result.calendar_oauth_initiated is True

    def test_calendar_skip_sets_disabled(self):
        state = _make_state(step="calendar")
        deps = _make_deps()
        deps.state_store.get_pending_users.return_value = []
        deps.state_store.get_workspace_config.return_value = None
        deps.state_store.complete_setup.return_value = None

        result = process_setup_message(
            text="", action_id="calendar_skip_setup", setup_state=state, deps=deps
        )

        assert result.calendar_enabled is False
        assert result.step == "done"

    def test_confirmation_writes_config(self):
        state = _make_state(
            step="confirmation",
            website_url="https://example.com",
            teams=("Eng",),
            channel_mapping={"eng": "C1"},
            calendar_enabled=True,
        )
        deps = _make_deps()
        deps.state_store.get_pending_users.return_value = []
        deps.state_store.get_workspace_config.return_value = None

        process_setup_message(text="", action_id=None, setup_state=state, deps=deps)

        deps.state_store.complete_setup.assert_called_once_with(
            workspace_id="W1",
            config_updates={
                "admin_user_id": "U_ADMIN",
                "website_url": "https://example.com",
                "teams": ["Eng"],
                "channel_mapping": {"eng": "C1"},
                "calendar_enabled": True,
            },
        )

    def test_confirmation_deletes_setup_record(self):
        """complete_setup internally deletes the SETUP record."""
        state = _make_state(step="confirmation")
        deps = _make_deps()
        deps.state_store.get_pending_users.return_value = []
        deps.state_store.get_workspace_config.return_value = None

        process_setup_message(text="", action_id=None, setup_state=state, deps=deps)

        # complete_setup is called which internally calls delete_setup_state
        deps.state_store.complete_setup.assert_called_once()

    def test_pending_users_enqueued_after_setup(self):
        state = _make_state(step="confirmation")
        deps = _make_deps()
        deps.state_store.get_workspace_config.return_value = None

        pending_plan = OnboardingPlan(
            workspace_id="W1",
            user_id="U_PENDING",
            user_name="Bob",
            role="engineer",
            status=PlanStatus.PENDING_SETUP,
            version=1,
            steps=[PlanStep(id=1, title="Welcome", status=StepStatus.PENDING)],
        )
        deps.state_store.get_pending_users.return_value = [pending_plan]

        process_setup_message(text="", action_id=None, setup_state=state, deps=deps)

        deps.sqs_client.send_message.assert_called()
        sqs_body = json.loads(deps.sqs_client.send_message.call_args[1]["MessageBody"])
        assert sqs_body["type"] == "onboard_user"
        assert sqs_body["user_id"] == "U_PENDING"


class TestSetupLLMFallback:
    def _make_llm_router(
        self, reply: str = "Please provide a valid URL like https://example.com."
    ) -> MagicMock:
        router = MagicMock()
        router.invoke.return_value = LLMResponse(
            text=reply,
            input_tokens=20,
            output_tokens=15,
            model_id="gemini-2.5-flash",
        )
        return router

    def test_unexpected_input_triggers_llm_call(self):
        """Non-URL input in awaiting_url step calls LLM router."""
        state = _make_state(step="awaiting_url")
        llm_router = self._make_llm_router()
        deps = _make_deps(llm_router=llm_router)

        process_setup_message(
            text="what do I do?",
            action_id=None,
            setup_state=state,
            deps=deps,
        )

        llm_router.invoke.assert_called_once()
        call_kwargs = llm_router.invoke.call_args[1]
        assert "awaiting_url" in call_kwargs["messages"][0]["content"]
        assert "what do I do?" in call_kwargs["messages"][0]["content"]

    def test_llm_response_guides_back_to_current_step(self):
        """LLM guidance message is sent to the admin via Slack."""
        guidance = "Please provide a full URL starting with https://."
        state = _make_state(step="awaiting_url")
        llm_router = self._make_llm_router(reply=guidance)
        deps = _make_deps(llm_router=llm_router)

        process_setup_message(
            text="I'm confused",
            action_id=None,
            setup_state=state,
            deps=deps,
        )

        deps.slack_client.send_message.assert_called_once()
        sent_text = deps.slack_client.send_message.call_args[1]["text"]
        assert sent_text == guidance

    def test_step_does_not_advance_after_fallback(self):
        """Setup step remains unchanged when LLM fallback is triggered."""
        state = _make_state(step="awaiting_url")
        llm_router = self._make_llm_router()
        deps = _make_deps(llm_router=llm_router)

        result = process_setup_message(
            text="not a url",
            action_id=None,
            setup_state=state,
            deps=deps,
        )

        assert result.step == "awaiting_url"
        assert result is state


class TestUrlValidation:
    def test_valid_https_url(self):
        assert _is_valid_url("https://example.com") is True

    def test_valid_http_url(self):
        assert _is_valid_url("http://example.com") is True

    def test_valid_url_with_path(self):
        assert _is_valid_url("https://example.com/about") is True

    def test_rejects_plain_text(self):
        assert _is_valid_url("not a url") is False

    def test_rejects_no_scheme(self):
        assert _is_valid_url("example.com") is False

    def test_rejects_empty_string(self):
        assert _is_valid_url("") is False

    def test_rejects_ftp(self):
        assert _is_valid_url("ftp://example.com") is False


class TestUnknownStep:
    def test_unknown_step_returns_state_unchanged(self):
        state = _make_state(step="nonexistent_step")
        deps = _make_deps()

        result = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )

        assert result is state


class TestAcquireLockTTL:
    def test_acquire_lock_uses_ttl_seconds_param(self):
        """acquire_lock passes ttl_seconds through to the DynamoDB item."""
        from state.dynamo import DynamoStateStore

        mock_table = MagicMock()
        store = DynamoStateStore(table=mock_table)

        store.acquire_lock(workspace_id="W1", user_id="U1", ttl_seconds=90)

        put_call = mock_table.put_item.call_args
        item = put_call.kwargs["Item"]
        # TTL should be ~90s from now
        import time

        now = int(time.time())
        assert abs(item["ttl"] - (now + 90)) < 5

    def test_acquire_lock_default_ttl_is_15(self):
        """Default ttl_seconds should be 15."""
        from state.dynamo import DynamoStateStore

        mock_table = MagicMock()
        store = DynamoStateStore(table=mock_table)

        store.acquire_lock(workspace_id="W1", user_id="U1")

        put_call = mock_table.put_item.call_args
        item = put_call.kwargs["Item"]
        import time

        now = int(time.time())
        assert abs(item["ttl"] - (now + 15)) < 5

    def test_acquire_lock_overwrites_expired_lock(self):
        """acquire_lock condition should allow overwriting expired locks."""
        from state.dynamo import DynamoStateStore

        mock_table = MagicMock()
        store = DynamoStateStore(table=mock_table)

        store.acquire_lock(workspace_id="W1", user_id="U1")

        put_call = mock_table.put_item.call_args
        condition = put_call.kwargs["ConditionExpression"]
        assert "attribute_not_exists(pk)" in condition
        assert "#ttl < :now" in condition
        assert put_call.kwargs["ExpressionAttributeNames"] == {"#ttl": "ttl"}
        assert ":now" in put_call.kwargs["ExpressionAttributeValues"]


class TestChannelMappingBlocks:
    def test_channel_mapping_has_confirm_button(self):
        """Channel mapping blocks should include a Confirm Mapping button."""
        from slack.blocks import channel_mapping

        default_ch = {"id": "C_GEN", "name": "general"}
        channels = [default_ch, {"id": "C_ENG", "name": "engineering"}]
        blocks = channel_mapping(
            teams=["Engineering"], channels=channels, default_channel=default_ch
        )

        # Find the actions block with confirm button
        action_blocks = [b for b in blocks if b.get("type") == "actions"]
        assert len(action_blocks) == 1
        confirm_btn = action_blocks[0]["elements"][0]
        assert confirm_btn["action_id"] == "channel_mapping_confirm"

    def test_channel_mapping_has_initial_option(self):
        """Each dropdown should have initial_option set to default_channel."""
        from slack.blocks import channel_mapping

        default_ch = {"id": "C_GEN", "name": "general"}
        channels = [default_ch, {"id": "C_ENG", "name": "engineering"}]
        blocks = channel_mapping(
            teams=["Engineering"], channels=channels, default_channel=default_ch
        )

        # Find the section block with accessory (dropdown)
        section_blocks = [b for b in blocks if b.get("accessory")]
        assert len(section_blocks) == 1
        select = section_blocks[0]["accessory"]
        assert select["initial_option"]["value"] == "C_GEN"
        assert select["initial_option"]["text"]["text"] == "general"

    def test_channel_mapping_has_help_note(self):
        """Channel mapping should include a note about creating channels."""
        from slack.blocks import channel_mapping

        default_ch = {"id": "C_GEN", "name": "general"}
        blocks = channel_mapping(
            teams=["Eng"], channels=[default_ch], default_channel=default_ch
        )

        texts = [
            b.get("text", {}).get("text", "")
            for b in blocks
            if b.get("type") == "section"
        ]
        assert any("/sherpa-setup" in t for t in texts)


class TestChannelMappingConfirm:
    def test_confirm_action_transitions_to_calendar(self):
        """channel_mapping_confirm action should transition to calendar step."""
        state = _make_state(
            step="channels",
            teams=("Engineering",),
            channel_mapping={"engineering": "C_GEN"},
        )
        deps = _make_deps()
        deps.state_store.get_pending_users.return_value = []

        result = process_setup_message(
            text="", action_id="channel_mapping_confirm", setup_state=state, deps=deps
        )

        assert result.step == "calendar"

    def test_confirm_with_defaults_uses_prepopulated_mapping(self):
        """Confirming without changing dropdowns should use the default mapping."""
        state = _make_state(
            step="channels",
            teams=("Engineering", "Marketing"),
            channel_mapping={"engineering": "C_GEN", "marketing": "C_GEN"},
        )
        deps = _make_deps()
        deps.state_store.get_pending_users.return_value = []

        result = process_setup_message(
            text="", action_id="channel_mapping_confirm", setup_state=state, deps=deps
        )

        assert result.step == "calendar"
        assert result.channel_mapping == {"engineering": "C_GEN", "marketing": "C_GEN"}

    def test_dropdown_updates_mapping_without_transition(self):
        """Dropdown selection should update mapping but not advance step."""
        state = _make_state(
            step="channels",
            teams=("Engineering", "Marketing"),
            channel_mapping={"engineering": "C_GEN", "marketing": "C_GEN"},
        )
        deps = _make_deps()

        result = process_setup_message(
            text="C_ENG",
            action_id="channel_map_engineering",
            setup_state=state,
            deps=deps,
        )

        assert result.step == "channels"
        assert result.channel_mapping["engineering"] == "C_ENG"


class TestTransitionToChannelsFindsGeneral:
    def test_general_channel_used_as_default(self):
        """_transition_to_channels should find #general and pre-populate mapping."""
        state = _make_state(step="teams", teams=("Engineering", "Marketing"))
        deps = _make_deps()
        deps.slack_client.list_channels.return_value = [
            {"id": "C_GEN", "name": "general", "is_general": True},
            {"id": "C_ENG", "name": "engineering"},
        ]

        result = process_setup_message(
            text="", action_id="teams_confirm", setup_state=state, deps=deps
        )

        assert result.step == "channels"
        # All teams pre-mapped to #general
        assert result.channel_mapping["engineering"] == "C_GEN"
        assert result.channel_mapping["marketing"] == "C_GEN"

    def test_fallback_to_name_general(self):
        """If is_general not present, match by name == 'general'."""
        state = _make_state(step="teams", teams=("Sales",))
        deps = _make_deps()
        deps.slack_client.list_channels.return_value = [
            {"id": "C_G", "name": "general"},
            {"id": "C_S", "name": "sales"},
        ]

        result = process_setup_message(
            text="", action_id="teams_confirm", setup_state=state, deps=deps
        )

        assert result.channel_mapping["sales"] == "C_G"

    def test_fallback_to_first_channel(self):
        """If no #general found, use first channel."""
        state = _make_state(step="teams", teams=("Sales",))
        deps = _make_deps()
        deps.slack_client.list_channels.return_value = [
            {"id": "C_RAND", "name": "random"},
        ]

        result = process_setup_message(
            text="", action_id="teams_confirm", setup_state=state, deps=deps
        )

        assert result.channel_mapping["sales"] == "C_RAND"


class TestSlackLinkStripping:
    def test_angle_bracket_url(self):
        """Slack auto-link <https://example.com> should be accepted."""
        state = _make_state(step="awaiting_url")
        deps = _make_deps()
        deps.slack_client.list_usergroups.return_value = []

        with patch("rag.scraper.scrape_site", side_effect=Exception("no network")):
            result = process_setup_message(
                text="<https://example.com>",
                action_id=None,
                setup_state=state,
                deps=deps,
            )

        assert result.website_url == "https://example.com"
        assert result.step == "teams"

    def test_pipe_label_url(self):
        """Slack rich-link <https://example.com|example.com> should be accepted."""
        state = _make_state(step="awaiting_url")
        deps = _make_deps()
        deps.slack_client.list_usergroups.return_value = []

        with patch("rag.scraper.scrape_site", side_effect=Exception("no network")):
            result = process_setup_message(
                text="<https://example.com|example.com>",
                action_id=None,
                setup_state=state,
                deps=deps,
            )

        assert result.website_url == "https://example.com"

    def test_bare_url_passthrough(self):
        """Plain URL without Slack formatting should work as-is."""
        state = _make_state(step="awaiting_url")
        deps = _make_deps()
        deps.slack_client.list_usergroups.return_value = []

        with patch("rag.scraper.scrape_site", side_effect=Exception("no network")):
            result = process_setup_message(
                text="https://example.com",
                action_id=None,
                setup_state=state,
                deps=deps,
            )

        assert result.website_url == "https://example.com"


class TestCalendarOAuthInitiatedField:
    def test_setup_state_defaults_to_false(self):
        state = _make_state(step="calendar")
        assert state.calendar_oauth_initiated is False

    def test_setup_state_accepts_true(self):
        state = _make_state(step="calendar", calendar_oauth_initiated=True)
        assert state.calendar_oauth_initiated is True


class TestResumeHandling:
    def test_awaiting_url_resume_sends_prompt(self):
        """Resume at awaiting_url (text='', action_id=None) re-sends URL prompt."""
        state = _make_state(step="awaiting_url")
        deps = _make_deps(llm_router=MagicMock())

        result = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )

        assert result.step == "awaiting_url"
        assert result is state
        msg = deps.slack_client.send_message.call_args[1]["text"]
        # Must be the resume prompt, NOT the LLM fallback error
        assert "share your company" in msg.lower()
        # LLM should NOT be called for empty resume input
        deps.llm_router.invoke.assert_not_called()

    def test_teams_resume_with_existing_teams_re_renders_blocks(self):
        """Resume at teams with state.teams populated re-renders team_confirmation."""
        state = _make_state(step="teams", teams=("Engineering", "Marketing"))
        deps = _make_deps()

        result = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )

        assert result.step == "teams"
        assert result is state
        call_kwargs = deps.slack_client.send_message.call_args[1]
        assert call_kwargs.get("blocks") is not None

    def test_teams_resume_without_teams_sends_manual_prompt(self):
        """Resume at teams with no teams sends manual input prompt."""
        state = _make_state(step="teams", teams=())
        deps = _make_deps()

        result = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )

        assert result.step == "teams"
        assert result is state
        msg = deps.slack_client.send_message.call_args[1]["text"]
        assert "team" in msg.lower()

    def test_calendar_resume_re_sends_prompt(self):
        """Resume at calendar (action_id=None) re-sends calendar_setup_prompt."""
        state = _make_state(step="calendar")
        deps = _make_deps()

        result = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )

        assert result.step == "calendar"
        assert result is state
        call_kwargs = deps.slack_client.send_message.call_args[1]
        assert call_kwargs.get("blocks") is not None


class TestCalendarStateOnEnable:
    def test_calendar_enable_sets_false_not_true(self):
        """Calendar enable should set calendar_enabled=False until OAuth completes."""
        state = _make_state(step="calendar")
        deps = _make_deps()
        deps.state_store.get_pending_users.return_value = []
        deps.state_store.get_workspace_config.return_value = None

        result = process_setup_message(
            text="", action_id="calendar_enable", setup_state=state, deps=deps
        )

        assert result.calendar_enabled is False

    def test_calendar_enable_sets_oauth_initiated(self):
        """Calendar enable should set calendar_oauth_initiated=True."""
        state = _make_state(step="calendar")
        deps = _make_deps()
        deps.state_store.get_pending_users.return_value = []
        deps.state_store.get_workspace_config.return_value = None

        result = process_setup_message(
            text="", action_id="calendar_enable", setup_state=state, deps=deps
        )

        assert result.calendar_oauth_initiated is True

    def test_summary_shows_pending_when_oauth_initiated(self):
        """Summary should say 'Pending authorization' when OAuth initiated but not completed."""
        state = _make_state(step="calendar")
        deps = _make_deps()
        deps.state_store.get_pending_users.return_value = []
        deps.state_store.get_workspace_config.return_value = None

        process_setup_message(
            text="", action_id="calendar_enable", setup_state=state, deps=deps
        )

        messages = [c[1]["text"] for c in deps.slack_client.send_message.call_args_list]
        summary_msg = [m for m in messages if "Setup Complete" in m]
        assert len(summary_msg) == 1
        assert "pending" in summary_msg[0].lower()

    def test_summary_preserves_existing_calendar_enabled(self):
        """If OAuth callback already set calendar_enabled=True on CONFIG, preserve it."""
        state = _make_state(step="calendar")
        deps = _make_deps()
        deps.state_store.get_pending_users.return_value = []
        # Simulate: OAuth callback already flipped calendar_enabled=True on CONFIG
        existing_config = WorkspaceConfig(
            workspace_id="W1",
            team_name="Test",
            bot_user_id="BOT1",
            calendar_enabled=True,
        )
        deps.state_store.get_workspace_config.return_value = existing_config

        process_setup_message(
            text="", action_id="calendar_enable", setup_state=state, deps=deps
        )

        # complete_setup should be called with calendar_enabled=True
        call_kwargs = deps.state_store.complete_setup.call_args[1]
        assert call_kwargs["config_updates"]["calendar_enabled"] is True


class TestConfirmationIdempotency:
    def test_second_confirmation_does_not_re_run_completion(self):
        """If setup_complete is already True, don't call complete_setup again."""
        state = _make_state(
            step="confirmation",
            website_url="https://example.com",
            teams=("Eng",),
            channel_mapping={"eng": "C1"},
        )
        deps = _make_deps()
        existing_config = WorkspaceConfig(
            workspace_id="W1",
            team_name="Test",
            bot_user_id="BOT1",
            setup_complete=True,
        )
        deps.state_store.get_workspace_config.return_value = existing_config

        result = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )

        assert result.step == "done"
        deps.state_store.complete_setup.assert_not_called()
        deps.state_store.get_pending_users.assert_not_called()
        # Summary message should still be sent
        deps.slack_client.send_message.assert_called_once()

    def test_first_confirmation_runs_full_completion(self):
        """First confirmation should run complete_setup and enqueue pending users."""
        state = _make_state(
            step="confirmation",
            website_url="https://example.com",
            teams=("Eng",),
            channel_mapping={"eng": "C1"},
        )
        deps = _make_deps()
        deps.state_store.get_workspace_config.return_value = None
        deps.state_store.get_pending_users.return_value = []

        result = process_setup_message(
            text="", action_id=None, setup_state=state, deps=deps
        )

        assert result.step == "done"
        deps.state_store.complete_setup.assert_called_once()
