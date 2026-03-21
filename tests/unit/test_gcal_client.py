"""Tests for GoogleCalendarClient."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from gcal.client import GoogleCalendarClient

_CLIENT_ID = "test-client-id"
_CLIENT_SECRET = "test-client-secret"
_ACCESS_TOKEN = "ya29.test-access-token"
_REFRESH_TOKEN = "1//test-refresh-token"


def _make_response(status_code: int, json_body: dict) -> MagicMock:
    """Build a mock httpx.Response."""
    mock = MagicMock(spec=httpx.Response)
    mock.status_code = status_code
    mock.json.return_value = json_body
    if status_code >= 400:
        mock.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=mock,
        )
    else:
        mock.raise_for_status.return_value = None
    return mock


class TestGoogleCalendarClient:
    def setup_method(self) -> None:
        self.client = GoogleCalendarClient(
            client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET
        )

    # ------------------------------------------------------------------
    # create_event
    # ------------------------------------------------------------------

    @patch("gcal.client.httpx.post")
    def test_create_event_success(self, mock_post: MagicMock) -> None:
        event_payload = {"id": "evt1", "summary": "Team Sync", "status": "confirmed"}
        mock_post.return_value = _make_response(200, event_payload)

        result = self.client.create_event(
            access_token=_ACCESS_TOKEN,
            summary="Team Sync",
            start="2024-01-15T10:00:00Z",
            end="2024-01-15T11:00:00Z",
        )

        assert result == event_payload
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["params"] == {"sendUpdates": "all"}
        assert call_kwargs.kwargs["headers"] == {
            "Authorization": f"Bearer {_ACCESS_TOKEN}"
        }
        body = call_kwargs.kwargs["json"]
        assert body["summary"] == "Team Sync"
        assert body["start"] == {"dateTime": "2024-01-15T10:00:00Z"}
        assert body["end"] == {"dateTime": "2024-01-15T11:00:00Z"}

    @patch("gcal.client.httpx.post")
    def test_create_event_with_attendees(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _make_response(200, {"id": "evt2"})

        self.client.create_event(
            access_token=_ACCESS_TOKEN,
            summary="Meeting",
            start="2024-01-15T14:00:00Z",
            end="2024-01-15T15:00:00Z",
            attendees=["alice@example.com", "bob@example.com"],
        )

        body = mock_post.call_args.kwargs["json"]
        assert body["attendees"] == [
            {"email": "alice@example.com"},
            {"email": "bob@example.com"},
        ]

    @patch("gcal.client.httpx.post")
    def test_create_event_sends_updates_all(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _make_response(200, {"id": "evt3"})

        self.client.create_event(
            access_token=_ACCESS_TOKEN,
            summary="Standup",
            start="2024-01-15T09:00:00Z",
            end="2024-01-15T09:15:00Z",
        )

        params = mock_post.call_args.kwargs["params"]
        assert params.get("sendUpdates") == "all"

    @patch("gcal.client.httpx.post")
    def test_create_event_api_error_raises(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _make_response(401, {"error": "unauthorized"})

        with pytest.raises(httpx.HTTPStatusError):
            self.client.create_event(
                access_token="bad-token",
                summary="Fail",
                start="2024-01-15T10:00:00Z",
                end="2024-01-15T11:00:00Z",
            )

    # ------------------------------------------------------------------
    # refresh_access_token
    # ------------------------------------------------------------------

    @patch("gcal.client.httpx.post")
    def test_refresh_access_token_success(self, mock_post: MagicMock) -> None:
        token_payload = {"access_token": "ya29.new-token", "expires_in": 3600}
        mock_post.return_value = _make_response(200, token_payload)

        result = self.client.refresh_access_token(refresh_token=_REFRESH_TOKEN)

        assert result == token_payload
        call_data = mock_post.call_args.kwargs["data"]
        assert call_data["grant_type"] == "refresh_token"
        assert call_data["refresh_token"] == _REFRESH_TOKEN
        assert call_data["client_id"] == _CLIENT_ID
        assert call_data["client_secret"] == _CLIENT_SECRET

    @patch("gcal.client.httpx.post")
    def test_refresh_access_token_invalid_grant_raises(
        self, mock_post: MagicMock
    ) -> None:
        error_payload = {
            "error": "invalid_grant",
            "error_description": "Token has been expired or revoked.",
        }
        mock_post.return_value = _make_response(400, error_payload)

        with pytest.raises(ValueError, match="invalid_grant"):
            self.client.refresh_access_token(refresh_token="expired-token")

    # ------------------------------------------------------------------
    # exchange_code
    # ------------------------------------------------------------------

    @patch("gcal.client.httpx.post")
    def test_exchange_code_success(self, mock_post: MagicMock) -> None:
        token_payload = {
            "access_token": "ya29.fresh",
            "refresh_token": "1//new-refresh",
            "expires_in": 3600,
        }
        mock_post.return_value = _make_response(200, token_payload)

        result = self.client.exchange_code(
            code="4/auth-code",
            redirect_uri="https://example.com/callback",
        )

        assert result == token_payload
        call_data = mock_post.call_args.kwargs["data"]
        assert call_data["grant_type"] == "authorization_code"
        assert call_data["code"] == "4/auth-code"
        assert call_data["redirect_uri"] == "https://example.com/callback"
        assert call_data["client_id"] == _CLIENT_ID
        assert call_data["client_secret"] == _CLIENT_SECRET
