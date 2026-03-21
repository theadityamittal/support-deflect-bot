"""Google Calendar raw HTTP client via httpx."""

from __future__ import annotations

from typing import Any, cast

import httpx

_CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleCalendarClient:
    """Thin HTTP client for the Google Calendar API.

    Raises:
        httpx.HTTPStatusError: On non-2xx responses (unless handled internally).
        ValueError: On OAuth errors such as invalid_grant.
    """

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret

    def create_event(
        self,
        *,
        access_token: str,
        summary: str,
        start: str,
        end: str,
        attendees: list[str] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a calendar event on the primary calendar.

        Args:
            access_token: OAuth2 access token.
            summary: Event title.
            start: ISO 8601 datetime string for event start.
            end: ISO 8601 datetime string for event end.
            attendees: Optional list of attendee email addresses.
            description: Optional event description.

        Returns:
            The created event resource dict from Google Calendar API.

        Raises:
            httpx.HTTPStatusError: On non-2xx API response.
        """
        body: dict[str, Any] = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }
        if attendees:
            body["attendees"] = [{"email": email} for email in attendees]

        response = httpx.post(
            _CALENDAR_EVENTS_URL,
            params={"sendUpdates": "all"},
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
        response.raise_for_status()
        return cast("dict[str, Any]", response.json())

    def refresh_access_token(self, *, refresh_token: str) -> dict[str, Any]:
        """Exchange a refresh token for a new access token.

        Args:
            refresh_token: The OAuth2 refresh token.

        Returns:
            Dict containing ``access_token`` and ``expires_in``.

        Raises:
            ValueError: When Google returns ``error: invalid_grant``.
            httpx.HTTPStatusError: On other non-2xx API responses.
        """
        response = httpx.post(
            _TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        payload: dict[str, Any] = response.json()
        if payload.get("error") == "invalid_grant":
            raise ValueError(
                f"invalid_grant: refresh token is invalid or has been revoked. "
                f"detail={payload.get('error_description', '')}"
            )
        response.raise_for_status()
        return payload

    def exchange_code(self, *, code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange an authorization code for access + refresh tokens.

        Args:
            code: The authorization code from Google's OAuth callback.
            redirect_uri: The redirect URI registered with the OAuth client.

        Returns:
            Dict containing ``access_token``, ``refresh_token``, and ``expires_in``.

        Raises:
            httpx.HTTPStatusError: On non-2xx API response.
        """
        response = httpx.post(
            _TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        response.raise_for_status()
        return cast("dict[str, Any]", response.json())
