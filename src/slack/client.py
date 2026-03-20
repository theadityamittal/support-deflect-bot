"""Thin wrapper around Slack WebClient for sending messages."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from slack_sdk import WebClient

logger = logging.getLogger(__name__)

_ALREADY_IN_CHANNEL = "already_in_channel"
_PAID_ONLY = "paid_only"


class SlackClient:
    """Wrapper around slack_sdk WebClient."""

    def __init__(self, *, web_client: WebClient) -> None:
        self._client = web_client

    def send_message(
        self,
        *,
        channel: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        thread_ts: str | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {"channel": channel, "text": text}
        if blocks is not None:
            kwargs["blocks"] = blocks
        if thread_ts is not None:
            kwargs["thread_ts"] = thread_ts
        response = self._client.chat_postMessage(**kwargs)
        ts: str = response.get("ts", "")
        return ts

    def send_ephemeral(self, *, channel: str, user: str, text: str) -> None:
        self._client.chat_postEphemeral(channel=channel, user=user, text=text)

    def update_message(
        self,
        *,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
        if blocks is not None:
            kwargs["blocks"] = blocks
        self._client.chat_update(**kwargs)

    def invite_to_channel(self, *, channel_id: str, user_id: str) -> bool:
        """Invite user to channel. Returns True (idempotent on already_in_channel)."""
        from slack_sdk.errors import SlackApiError

        try:
            self._client.conversations_invite(channel=channel_id, users=user_id)
            return True
        except SlackApiError as e:
            if _ALREADY_IN_CHANNEL in str(e):
                return True
            raise

    def get_user_email(self, *, user_id: str) -> str | None:
        """Return email from user profile, or None if unavailable."""
        response = self._client.users_info(user=user_id)
        user_data = response.get("user") or {}
        profile: dict[str, Any] = user_data.get("profile", {})
        email = profile.get("email")
        return str(email) if email else None

    def list_channels(self) -> list[dict[str, Any]]:
        """Return list of public channel dicts."""
        response = self._client.conversations_list(types="public_channel")
        channels: list[dict[str, Any]] = response.get("channels", [])
        return channels

    def list_usergroups(self) -> list[dict[str, Any]]:
        """Return list of usergroup dicts. Returns [] on free-plan (paid_only) error."""
        from slack_sdk.errors import SlackApiError

        try:
            response = self._client.usergroups_list()
            groups: list[dict[str, Any]] = response.get("usergroups", [])
            return groups
        except SlackApiError as e:
            if _PAID_ONLY in str(e):
                logger.debug("list_usergroups: paid_only error, returning empty list")
                return []
            raise
