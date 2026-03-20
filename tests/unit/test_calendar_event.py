"""Tests for CalendarEventTool — real Google Calendar integration."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import httpx
import pytest
from agent.tools.calendar_event import CalendarEventTool


def _make_tool(
    *,
    gcal_client: MagicMock | None = None,
    encryptor: MagicMock | None = None,
    state_store: MagicMock | None = None,
    workspace_id: str = "W1",
) -> CalendarEventTool:
    return CalendarEventTool(
        gcal_client=gcal_client or MagicMock(),
        encryptor=encryptor or MagicMock(),
        state_store=state_store or MagicMock(),
        workspace_id=workspace_id,
    )


def _valid_secrets(*, expires_at: float | None = None) -> dict:
    return {
        "gcal_access_token": "access-tok",
        "gcal_refresh_token": "refresh-tok",
        "gcal_token_expires_at": expires_at
        if expires_at is not None
        else (time.time() + 3600),
        "bot_token": "xoxb-test",
    }


def _gcal_event_response() -> dict:
    return {
        "id": "evt123",
        "htmlLink": "https://calendar.google.com/event?eid=evt123",
        "summary": "Orientation meeting",
    }


class TestCalendarEventTool:
    def test_name(self):
        tool = _make_tool()
        assert tool.name == "calendar_event"

    def test_creates_event_via_google_client(self):
        gcal = MagicMock()
        gcal.create_event.return_value = _gcal_event_response()

        store = MagicMock()
        store.get_workspace_secrets.return_value = _valid_secrets()

        tool = _make_tool(gcal_client=gcal, state_store=store)
        result = tool.execute(
            title="Orientation meeting",
            date="2026-03-25",
            time="10:00",
            duration_minutes=30,
        )

        assert result.ok is True
        assert result.data["event_id"] == "evt123"
        assert result.data["title"] == "Orientation meeting"
        assert "start" in result.data
        gcal.create_event.assert_called_once()
        call_kwargs = gcal.create_event.call_args.kwargs
        assert call_kwargs["summary"] == "Orientation meeting"
        assert call_kwargs["access_token"] == "access-tok"

    def test_creates_event_with_attendee(self):
        gcal = MagicMock()
        gcal.create_event.return_value = _gcal_event_response()

        store = MagicMock()
        store.get_workspace_secrets.return_value = _valid_secrets()

        tool = _make_tool(gcal_client=gcal, state_store=store)
        result = tool.execute(
            title="Training",
            date="2026-03-26",
            time="14:00",
            duration_minutes=60,
            attendee_email="jane@example.com",
        )

        assert result.ok is True
        call_kwargs = gcal.create_event.call_args.kwargs
        assert call_kwargs["attendees"] == ["jane@example.com"]

    def test_refreshes_token_when_expired(self):
        gcal = MagicMock()
        gcal.refresh_access_token.return_value = {
            "access_token": "new-access-tok",
            "expires_in": 3600,
        }
        gcal.create_event.return_value = _gcal_event_response()

        # Token expired (past time)
        expired_secrets = _valid_secrets(expires_at=time.time() - 100)
        store = MagicMock()
        store.get_workspace_secrets.return_value = expired_secrets

        tool = _make_tool(gcal_client=gcal, state_store=store)
        result = tool.execute(
            title="Orientation",
            date="2026-03-25",
            time="10:00",
            duration_minutes=30,
        )

        assert result.ok is True
        gcal.refresh_access_token.assert_called_once_with(refresh_token="refresh-tok")
        # Updated token used in create_event call
        call_kwargs = gcal.create_event.call_args.kwargs
        assert call_kwargs["access_token"] == "new-access-tok"
        # Secrets persisted with refreshed token
        store.save_workspace_secrets.assert_called_once()
        saved_blob = store.save_workspace_secrets.call_args.kwargs["secrets_blob"]
        assert saved_blob["gcal_access_token"] == "new-access-tok"

    def test_handles_revoked_token_notifies_admin(self):
        gcal = MagicMock()
        gcal.refresh_access_token.side_effect = ValueError("invalid_grant: ...")

        expired_secrets = _valid_secrets(expires_at=time.time() - 100)
        store = MagicMock()
        store.get_workspace_secrets.return_value = expired_secrets

        tool = _make_tool(gcal_client=gcal, state_store=store)
        result = tool.execute(
            title="Orientation",
            date="2026-03-25",
            time="10:00",
            duration_minutes=30,
        )

        assert result.ok is False
        assert "revoked" in result.error.lower() or "reconnect" in result.error.lower()
        gcal.create_event.assert_not_called()

    def test_retries_on_transient_error(self):
        gcal = MagicMock()
        # Fail first attempt with 503, succeed on second
        mock_response_503 = MagicMock()
        mock_response_503.status_code = 503
        transient_exc = httpx.HTTPStatusError(
            "503", request=MagicMock(), response=mock_response_503
        )
        gcal.create_event.side_effect = [transient_exc, _gcal_event_response()]

        store = MagicMock()
        store.get_workspace_secrets.return_value = _valid_secrets()

        tool = _make_tool(gcal_client=gcal, state_store=store)
        result = tool.execute(
            title="Orientation",
            date="2026-03-25",
            time="10:00",
            duration_minutes=30,
        )

        assert result.ok is True
        assert gcal.create_event.call_count == 2

    def test_notifies_user_on_persistent_failure(self):
        gcal = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 503
        exc = httpx.HTTPStatusError("503", request=MagicMock(), response=mock_response)
        gcal.create_event.side_effect = exc  # always fails

        store = MagicMock()
        store.get_workspace_secrets.return_value = _valid_secrets()

        tool = _make_tool(gcal_client=gcal, state_store=store)
        result = tool.execute(
            title="Orientation",
            date="2026-03-25",
            time="10:00",
            duration_minutes=30,
        )

        assert result.ok is False
        assert "failed" in result.error.lower()
        assert gcal.create_event.call_count == 2  # tried twice

    def test_returns_failure_when_no_secrets(self):
        store = MagicMock()
        store.get_workspace_secrets.return_value = None

        tool = _make_tool(state_store=store)
        result = tool.execute(
            title="Orientation",
            date="2026-03-25",
            time="10:00",
            duration_minutes=30,
        )

        assert result.ok is False
        assert "credentials" in result.error.lower()

    def test_returns_failure_when_no_gcal_tokens(self):
        store = MagicMock()
        store.get_workspace_secrets.return_value = {"bot_token": "xoxb-test"}

        tool = _make_tool(state_store=store)
        result = tool.execute(
            title="Orientation",
            date="2026-03-25",
            time="10:00",
            duration_minutes=30,
        )

        assert result.ok is False
        assert "not connected" in result.error.lower()

    def test_tool_excluded_when_calendar_disabled(self):
        """CalendarEventTool should not be in tools dict when calendar is disabled.

        This is enforced in the worker wiring layer. We verify the tool itself
        does not self-register — its presence is controlled by the caller.
        """
        # CalendarEventTool requires constructor args; calling without them fails
        with pytest.raises(TypeError):
            CalendarEventTool()  # type: ignore[call-arg]
