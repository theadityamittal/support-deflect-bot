"""calendar_event tool — creates Google Calendar events via GoogleCalendarClient."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from agent.tools.base import AgentTool, ToolResult

if TYPE_CHECKING:
    from gcal.client import GoogleCalendarClient
    from security.crypto import FieldEncryptor
    from state.dynamo import DynamoStateStore

logger = logging.getLogger(__name__)

_TOKEN_EXPIRY_BUFFER_SECONDS = 300  # refresh 5 minutes before expiry


class CalendarEventTool(AgentTool):
    """Create a Google Calendar event using the workspace's OAuth tokens."""

    def __init__(
        self,
        *,
        gcal_client: GoogleCalendarClient,
        encryptor: FieldEncryptor,
        state_store: DynamoStateStore,
        workspace_id: str,
    ) -> None:
        self._gcal = gcal_client
        self._encryptor = encryptor
        self._store = state_store
        self._workspace_id = workspace_id

    @property
    def name(self) -> str:
        return "calendar_event"

    @property
    def description(self) -> str:
        return "Schedule a Google Calendar event (orientation, training, etc.)."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "date": {"type": "string", "description": "Date (YYYY-MM-DD)"},
                "time": {"type": "string", "description": "Time (HH:MM)"},
                "duration_minutes": {"type": "integer", "description": "Duration"},
                "attendee_email": {
                    "type": "string",
                    "description": "Optional attendee",
                },
            },
            "required": ["title", "date", "time", "duration_minutes"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        title: str = kwargs.get("title", "")
        date: str = kwargs.get("date", "")
        time_str: str = kwargs.get("time", "")
        duration_minutes: int = int(kwargs.get("duration_minutes", 30))
        attendee_email: str | None = kwargs.get("attendee_email")

        # Build ISO 8601 start/end datetimes
        start_dt = datetime.fromisoformat(f"{date}T{time_str}:00")
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        start_iso = start_dt.isoformat()
        end_iso = end_dt.isoformat()

        attendees = [attendee_email] if attendee_email else None

        # 1. Load workspace secrets
        secrets = self._store.get_workspace_secrets(
            workspace_id=self._workspace_id, encryptor=self._encryptor
        )
        if not secrets:
            return ToolResult.failure(
                error="No calendar credentials found for this workspace"
            )

        access_token: str = secrets.get("gcal_access_token", "")
        refresh_token: str = secrets.get("gcal_refresh_token", "")
        expires_at: float = float(secrets.get("gcal_token_expires_at", 0))

        if not access_token or not refresh_token:
            return ToolResult.failure(
                error="Google Calendar not connected for this workspace"
            )

        # 2. Check token expiry and refresh if needed
        now = time.time()
        if expires_at - now < _TOKEN_EXPIRY_BUFFER_SECONDS:
            try:
                token_data = self._gcal.refresh_access_token(
                    refresh_token=refresh_token
                )
                access_token = token_data["access_token"]
                expires_in: int = int(token_data.get("expires_in", 3600))
                expires_at = now + expires_in

                # 3. Persist refreshed tokens
                updated_secrets = dict(secrets)
                updated_secrets["gcal_access_token"] = access_token
                updated_secrets["gcal_token_expires_at"] = expires_at
                self._store.save_workspace_secrets(
                    workspace_id=self._workspace_id,
                    secrets_blob=updated_secrets,
                    encryptor=self._encryptor,
                )
                logger.info(
                    "calendar_event: access token refreshed for workspace=%s",
                    self._workspace_id,
                )
            except ValueError as exc:
                # invalid_grant — token revoked
                logger.warning(
                    "calendar_event: token revoked for workspace=%s — %s",
                    self._workspace_id,
                    exc,
                )
                return ToolResult.failure(
                    error=(
                        "Google Calendar access has been revoked. "
                        "Please ask a workspace admin to reconnect the Google account."
                    )
                )

        # 4. Create the event; retry once on transient HTTP errors
        for attempt in range(2):
            try:
                event = self._gcal.create_event(
                    access_token=access_token,
                    summary=title,
                    start=start_iso,
                    end=end_iso,
                    attendees=attendees,
                )
                logger.info(
                    "calendar_event: created '%s' (id=%s) for workspace=%s",
                    title,
                    event.get("id"),
                    self._workspace_id,
                )
                return ToolResult.success(
                    data={
                        "event_id": event.get("id"),
                        "title": title,
                        "start": start_iso,
                        "end": end_iso,
                        "html_link": event.get("htmlLink", ""),
                    }
                )
            except httpx.HTTPStatusError as exc:
                if attempt == 0 and _is_transient(exc):
                    logger.warning(
                        "calendar_event: transient error on attempt %d, retrying — %s",
                        attempt + 1,
                        exc,
                    )
                    continue
                logger.exception("calendar_event: failed to create event")
                return ToolResult.failure(
                    error=(
                        "Failed to create the calendar event. "
                        "Please try again or contact a workspace admin."
                    )
                )

        # Exhausted retries
        return ToolResult.failure(
            error=(
                "Failed to create the calendar event after retrying. "
                "Please try again or contact a workspace admin."
            )
        )


def _is_transient(exc: httpx.HTTPStatusError) -> bool:
    """Return True for HTTP status codes that are worth retrying (5xx, 429)."""
    status: int = exc.response.status_code
    return status == 429 or status >= 500
