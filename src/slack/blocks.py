"""Slack Block Kit builder functions for setup and calendar interactions.

All functions are pure — no side effects, no Slack API calls.
Each returns a list[dict] compatible with chat_postMessage(blocks=...).
"""

from __future__ import annotations

import re


def _slug(text: str) -> str:
    """Convert a team name to a safe action_id suffix (lowercase, underscores)."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _button(text: str, action_id: str, style: str | None = None) -> dict:
    element: dict = {
        "type": "button",
        "text": {"type": "plain_text", "text": text, "emoji": False},
        "action_id": action_id,
    }
    if style:
        element["style"] = style
    return element


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _actions(*elements: dict) -> dict:
    return {"type": "actions", "elements": list(elements)}


def calendar_confirmation(
    title: str,
    date: str,
    time: str,
    attendees: list[str],
) -> list[dict]:
    """Build blocks to confirm or skip adding a calendar event.

    Args:
        title: Event title.
        date: Event date string (e.g. "2025-03-20").
        time: Event time string (e.g. "10:00 AM").
        attendees: List of attendee email addresses.

    Returns:
        Block Kit blocks list.
    """
    attendee_text = ", ".join(attendees) if attendees else "_No attendees_"
    detail_text = (
        f"*{title}*\n:calendar: {date} at {time}\n:busts_in_silhouette: {attendee_text}"
    )
    return [
        _section(detail_text),
        _actions(
            _button("Confirm", "calendar_confirm", style="primary"),
            _button("Skip", "calendar_skip"),
        ),
    ]


def calendar_setup_prompt() -> list[dict]:
    """Build blocks prompting admin to enable Google Calendar integration.

    Returns:
        Block Kit blocks list.
    """
    return [
        _section(
            "*Google Calendar Integration*\n"
            "Would you like to enable Google Calendar integration? "
            "This allows the bot to schedule meetings and send calendar invites."
        ),
        _actions(
            _button("Enable", "calendar_enable", style="primary"),
            _button("Skip", "calendar_skip_setup"),
        ),
    ]


def channel_mapping(
    teams: list[str],
    channels: list[dict],
    *,
    default_channel: dict | None = None,
) -> list[dict]:
    """Build blocks with a channel select dropdown for each team.

    Args:
        teams: Team names to map.
        channels: Available channels as dicts with 'id' and 'name' keys.
        default_channel: Channel dict to pre-select on every dropdown.

    Returns:
        Block Kit blocks list.
    """
    options = [
        {
            "text": {"type": "plain_text", "text": ch["name"]},
            "value": ch["id"],
        }
        for ch in channels
    ]

    blocks: list[dict] = [
        _section("*Channel Mapping*\nSelect a Slack channel for each team.")
    ]

    initial_option = None
    if default_channel:
        initial_option = {
            "text": {"type": "plain_text", "text": default_channel["name"]},
            "value": default_channel["id"],
        }

    for team in teams:
        action_id = f"channel_map_{_slug(team)}"
        select: dict = {
            "type": "static_select",
            "placeholder": {"type": "plain_text", "text": "Select a channel"},
            "action_id": action_id,
            "options": options,
        }
        if initial_option:
            select["initial_option"] = initial_option
        block: dict = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{team}*"},
            "accessory": select,
        }
        blocks.append(block)

    blocks.append(
        _section(
            "Don't see your channel? Create it in Slack first, "
            "then run `/sherpa-setup` to resume."
        )
    )
    blocks.append(
        _actions(_button("Confirm Mapping", "channel_mapping_confirm", style="primary"))
    )

    return blocks


def team_confirmation(teams: list[str]) -> list[dict]:
    """Build blocks showing detected teams with confirm/edit actions.

    Args:
        teams: List of detected team names.

    Returns:
        Block Kit blocks list.
    """
    if teams:
        team_list = "\n".join(f"• {t}" for t in teams)
        body = f"*Detected Teams*\nThe following teams were found:\n\n{team_list}"
    else:
        body = "*Detected Teams*\nNo teams were detected."

    return [
        _section(body),
        _actions(
            _button("Confirm", "teams_confirm", style="primary"),
            _button("Edit", "teams_edit"),
        ),
    ]


def relink_calendar(current_email: str) -> list[dict]:
    """Build blocks to relink or cancel the Google Calendar account.

    Args:
        current_email: Currently linked Google account email.

    Returns:
        Block Kit blocks list.
    """
    return [
        _section(
            f"*Google Calendar Account*\n"
            f"Currently linked: `{current_email}`\n"
            "Would you like to relink with a different account?"
        ),
        _actions(
            _button("Relink", "calendar_relink", style="primary"),
            _button("Cancel", "calendar_cancel"),
        ),
    ]
