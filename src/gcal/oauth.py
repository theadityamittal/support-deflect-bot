"""Google OAuth2 authorization URL builder for calendar access."""

from __future__ import annotations

import urllib.parse

_AUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"


def build_authorization_url(
    *, client_id: str, redirect_uri: str, workspace_id: str
) -> str:
    """Build Google OAuth2 authorization URL for calendar access.

    Args:
        client_id: Google OAuth2 client ID.
        redirect_uri: URI to redirect to after authorization.
        workspace_id: Slack workspace ID used as state to identify workspace in callback.

    Returns:
        Full authorization URL with query parameters.
    """
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _CALENDAR_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": workspace_id,
    }
    return f"{_AUTH_BASE_URL}?{urllib.parse.urlencode(params)}"
