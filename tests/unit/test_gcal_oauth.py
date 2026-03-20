"""Tests for Google OAuth authorization URL builder."""

from __future__ import annotations

import urllib.parse

from gcal.oauth import build_authorization_url


class TestGoogleOAuth:
    def _parse_url(self, url: str) -> tuple[str, dict[str, str]]:
        parsed = urllib.parse.urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        params = dict(urllib.parse.parse_qsl(parsed.query))
        return base, params

    def test_build_authorization_url(self) -> None:
        url = build_authorization_url(
            client_id="test-client-id",
            redirect_uri="https://example.com/callback",
            workspace_id="W123",
        )
        base, params = self._parse_url(url)
        assert base == "https://accounts.google.com/o/oauth2/v2/auth"
        assert params["client_id"] == "test-client-id"
        assert params["redirect_uri"] == "https://example.com/callback"
        assert params["response_type"] == "code"
        assert params["state"] == "W123"

    def test_build_url_includes_offline_access(self) -> None:
        url = build_authorization_url(
            client_id="test-client-id",
            redirect_uri="https://example.com/callback",
            workspace_id="W123",
        )
        _, params = self._parse_url(url)
        assert params["access_type"] == "offline"

    def test_build_url_includes_consent_prompt(self) -> None:
        url = build_authorization_url(
            client_id="test-client-id",
            redirect_uri="https://example.com/callback",
            workspace_id="W123",
        )
        _, params = self._parse_url(url)
        assert params["prompt"] == "consent"

    def test_build_url_includes_calendar_scope(self) -> None:
        url = build_authorization_url(
            client_id="test-client-id",
            redirect_uri="https://example.com/callback",
            workspace_id="W123",
        )
        _, params = self._parse_url(url)
        assert params["scope"] == "https://www.googleapis.com/auth/calendar.events"
