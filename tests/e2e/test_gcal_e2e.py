"""E2E tests: Google Calendar API with real credentials.

Tests token refresh, event creation/deletion, and error handling
against the live Google Calendar API. All tests skip when
GOOGLE_REFRESH_TOKEN is not set.

Run: .venv/bin/pytest tests/e2e/test_gcal_e2e.py -v -m e2e --no-cov -s
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from gcal.client import GoogleCalendarClient

_CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


@pytest.mark.e2e
class TestGoogleCalendarE2E:
    """Tests hitting real Google Calendar API with seeded refresh token."""

    def test_token_refresh(self, google_credentials, google_refresh_token):
        """Refresh token should return a valid access token."""
        client = GoogleCalendarClient(
            google_credentials["client_id"],
            google_credentials["client_secret"],
        )

        try:
            result = client.refresh_access_token(refresh_token=google_refresh_token)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                pytest.skip("Google refresh token expired (7-day Testing mode limit)")
            raise

        assert "access_token" in result
        assert len(result["access_token"]) > 0
        assert "expires_in" in result
        print(f"  Token refreshed: expires_in={result['expires_in']}s")

    def test_create_and_delete_event(self, google_credentials, google_refresh_token):
        """Create a test event, verify it exists, then delete it."""
        client = GoogleCalendarClient(
            google_credentials["client_id"],
            google_credentials["client_secret"],
        )

        # Get fresh access token
        try:
            token_data = client.refresh_access_token(refresh_token=google_refresh_token)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                pytest.skip("Google refresh token expired (7-day Testing mode limit)")
            raise
        access_token = token_data["access_token"]

        # Create event 1 hour from now
        now = datetime.now(UTC)
        start = (now + timedelta(hours=1)).isoformat()
        end = (now + timedelta(hours=2)).isoformat()

        event_id = None
        try:
            event = client.create_event(
                access_token=access_token,
                summary="E2E Test Event — auto-created, safe to delete",
                start=start,
                end=end,
                description="Created by Sherpa E2E test suite. Will be deleted immediately.",
            )

            assert "id" in event
            event_id = event["id"]
            print(f"  Created event: {event_id}")

            # Delete the event via raw HTTP (GoogleCalendarClient doesn't have delete)
            delete_response = httpx.delete(
                f"{_CALENDAR_EVENTS_URL}/{event_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            assert delete_response.status_code == 204
            print(f"  Deleted event: {event_id}")
        finally:
            if event_id is not None:
                with contextlib.suppress(Exception):
                    httpx.delete(
                        f"{_CALENDAR_EVENTS_URL}/{event_id}",
                        headers={"Authorization": f"Bearer {access_token}"},
                    )

    def test_create_event_with_invalid_token(self, google_credentials):
        """Invalid access token should raise HTTPStatusError."""
        client = GoogleCalendarClient(
            google_credentials["client_id"],
            google_credentials["client_secret"],
        )

        now = datetime.now(UTC)
        start = (now + timedelta(hours=1)).isoformat()
        end = (now + timedelta(hours=2)).isoformat()

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            client.create_event(
                access_token="invalid_token_e2e_test",
                summary="Should fail",
                start=start,
                end=end,
            )

        assert exc_info.value.response.status_code in (401, 403)
        print(
            f"  Invalid token correctly rejected: {exc_info.value.response.status_code}"
        )
