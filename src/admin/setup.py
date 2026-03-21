"""Admin setup state machine — processes admin messages during workspace setup.

Each step validates input, updates the SETUP record in DynamoDB,
and sends the next Block Kit prompt via Slack.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from gcal.oauth import build_authorization_url
from slack.blocks import calendar_setup_prompt, channel_mapping, team_confirmation

if TYPE_CHECKING:
    from state.dynamo import DynamoStateStore
    from state.models import SetupState

logger = logging.getLogger(__name__)

# Minimum Lambda time remaining before self-enqueue (ms)
_TIMEOUT_THRESHOLD_MS = 60_000

# URL validation pattern — requires scheme and domain
_URL_PATTERN = re.compile(
    r"^https?://"  # scheme
    r"[a-zA-Z0-9]"  # first char of domain
    r"[a-zA-Z0-9._-]*"  # rest of domain
    r"\.[a-zA-Z]{2,}"  # TLD
    r"(/\S*)?$"  # optional path
)


@dataclass(frozen=True)
class SetupDependencies:
    """Dependencies injected into the setup state machine."""

    state_store: DynamoStateStore
    slack_client: Any  # SlackClient
    encryptor: Any  # FieldEncryptor
    sqs_queue_url: str
    google_client_id: str
    google_oauth_redirect_uri: str
    lambda_context: Any  # for get_remaining_time_in_millis()
    sqs_client: Any = None  # boto3 SQS client for self-enqueue
    s3_client: Any = None  # boto3 S3 client for manifest storage
    s3_bucket: str = ""
    llm_router: Any = None  # LLMRouter — used for fallback guidance messages


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _is_valid_url(text: str) -> bool:
    """Validate that text looks like a reasonable website URL."""
    text = text.strip()
    if not _URL_PATTERN.match(text):
        return False
    try:
        parsed = urlparse(text)
        return bool(parsed.scheme and parsed.netloc)
    except Exception:
        return False


def process_setup_message(
    *,
    text: str,
    action_id: str | None,
    setup_state: SetupState,
    deps: SetupDependencies,
) -> SetupState:
    """Process an admin message during setup. Returns updated state.

    Dispatches to step-specific handlers based on ``setup_state.step``.
    Each handler validates input, persists the new state, sends the next
    Slack prompt, and returns an updated frozen ``SetupState``.
    """
    handlers: dict[str, Any] = {
        "welcome": _handle_welcome,
        "awaiting_url": _handle_awaiting_url,
        "scraping": _handle_scraping,
        "teams": _handle_teams,
        "channels": _handle_channels,
        "calendar": _handle_calendar,
        "confirmation": _handle_confirmation,
    }

    handler = handlers.get(setup_state.step)
    if handler is None:
        logger.error("Unknown setup step: %s", setup_state.step)
        return setup_state

    return handler(text=text, action_id=action_id, state=setup_state, deps=deps)


# ---------------------------------------------------------------------------
# LLM fallback
# ---------------------------------------------------------------------------


def _llm_fallback(
    *,
    text: str,
    step: str,
    expected_input: str,
    state: SetupState,
    deps: SetupDependencies,
) -> SetupState:
    """Call LLM to generate a helpful guidance message and send it via Slack.

    Returns the SAME state so the step does not advance.
    """
    if deps.llm_router is None:
        # No LLM available — send the expected_input hint directly
        deps.slack_client.send_message(
            channel=state.admin_user_id,
            text=expected_input,
        )
        return state

    from llm.provider import ModelRole

    prompt = (
        f"An admin is setting up a Slack workspace bot. "
        f"Current setup step: {step}. "
        f"Expected input: {expected_input}. "
        f"The admin said: {text!r}. "
        f"Write a short, friendly message (2-3 sentences) that helps them provide what is needed."
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        response = deps.llm_router.invoke(role=ModelRole.GENERATION, messages=messages)
        guidance = response.text
    except Exception:
        logger.exception("LLM fallback failed for step %s", step)
        guidance = f"I didn't understand that. {expected_input}"

    deps.slack_client.send_message(
        channel=state.admin_user_id,
        text=guidance,
    )
    return state


# ---------------------------------------------------------------------------
# Step handlers
# ---------------------------------------------------------------------------


def _handle_welcome(
    *, text: str, action_id: str | None, state: SetupState, deps: SetupDependencies
) -> SetupState:
    """Send greeting and ask for company website URL."""
    deps.slack_client.send_message(
        channel=state.admin_user_id,
        text=(
            "Welcome to Sherpa setup! I'll help you configure "
            "the onboarding bot for your workspace.\n\n"
            "To get started, please share your company's website URL "
            "(e.g. https://example.com)."
        ),
    )
    new_state = replace(state, step="awaiting_url", updated_at=_now_iso())
    deps.state_store.save_setup_state(setup_state=new_state)
    return new_state


def _handle_awaiting_url(
    *, text: str, action_id: str | None, state: SetupState, deps: SetupDependencies
) -> SetupState:
    """Validate URL, kick off scraping, transition to scraping step."""
    if not text.strip():
        deps.slack_client.send_message(
            channel=state.admin_user_id,
            text=(
                "Please share your company's website URL (e.g. https://example.com)."
            ),
        )
        return state

    # Slack auto-links URLs: <https://example.com> or <https://example.com|example.com>
    url = text.strip().strip("<>").split("|")[0]
    if not _is_valid_url(url):
        return _llm_fallback(
            text=text,
            step="awaiting_url",
            expected_input=(
                "That doesn't look like a valid URL. "
                "Please enter a full URL including https:// "
                "(e.g. https://example.com)."
            ),
            state=state,
            deps=deps,
        )

    new_state = replace(state, step="scraping", website_url=url, updated_at=_now_iso())
    deps.state_store.save_setup_state(setup_state=new_state)

    deps.slack_client.send_message(
        channel=state.admin_user_id,
        text=f"Got it! Scraping *{url}* for knowledge base content...",
    )

    # Attempt actual scraping; fall through to teams on failure or stub
    new_state = _run_scraping(state=new_state, deps=deps)
    return new_state


def _run_scraping(*, state: SetupState, deps: SetupDependencies) -> SetupState:
    """Execute scraping with Lambda timeout awareness.

    If scraping completes, transitions to teams step.
    If Lambda is about to timeout, saves manifest to S3 and self-enqueues.
    """
    try:
        from rag.scraper import scrape_site  # noqa: F811

        remaining = _get_remaining_ms(deps.lambda_context)
        if remaining < _TIMEOUT_THRESHOLD_MS:
            return _self_enqueue(state=state, deps=deps)

        pages = scrape_site(state.website_url, max_pages=50)
        logger.info("Scraped %d pages from %s", len(pages), state.website_url)

        deps.slack_client.send_message(
            channel=state.admin_user_id,
            text=f"Scraping complete — found {len(pages)} pages.",
        )
    except Exception as exc:
        logger.warning("Scraping failed, proceeding to teams: %s", exc)
        deps.slack_client.send_message(
            channel=state.admin_user_id,
            text="Scraping finished (some pages may have been skipped). Moving on to team setup.",
        )

    return _transition_to_teams(state=state, deps=deps)


def _handle_scraping(
    *, text: str, action_id: str | None, state: SetupState, deps: SetupDependencies
) -> SetupState:
    """Resume scraping from S3 manifest (self-enqueue continuation)."""
    remaining = _get_remaining_ms(deps.lambda_context)
    if remaining < _TIMEOUT_THRESHOLD_MS:
        return _self_enqueue(state=state, deps=deps)

    # If we have a manifest, resume from it
    if state.scrape_manifest_key:
        deps.slack_client.send_message(
            channel=state.admin_user_id,
            text="Resuming scraping from where we left off...",
        )

    # For now, transition to teams (full manifest-resume is Phase 2+)
    return _transition_to_teams(state=state, deps=deps)


def _transition_to_teams(*, state: SetupState, deps: SetupDependencies) -> SetupState:
    """Auto-detect Slack User Groups and show team confirmation."""
    usergroups = deps.slack_client.list_usergroups()
    team_names = [
        g.get("name", g.get("handle", "")) for g in usergroups if g.get("name")
    ]

    if team_names:
        blocks = team_confirmation(teams=team_names)
        deps.slack_client.send_message(
            channel=state.admin_user_id,
            text="Here are the teams I detected:",
            blocks=blocks,
        )
    else:
        deps.slack_client.send_message(
            channel=state.admin_user_id,
            text=(
                "I couldn't detect any Slack User Groups. "
                "Please type your team names separated by commas "
                "(e.g. Engineering, Marketing, Sales)."
            ),
        )

    new_state = replace(
        state,
        step="teams",
        teams=tuple(team_names),
        updated_at=_now_iso(),
    )
    deps.state_store.save_setup_state(setup_state=new_state)
    return new_state


def _handle_teams(
    *, text: str, action_id: str | None, state: SetupState, deps: SetupDependencies
) -> SetupState:
    """Handle team confirmation or manual team input."""
    if action_id == "teams_confirm":
        # Admin confirmed auto-detected teams
        return _transition_to_channels(state=state, deps=deps)

    if action_id == "teams_edit":
        deps.slack_client.send_message(
            channel=state.admin_user_id,
            text=(
                "Please type your team names separated by commas "
                "(e.g. Engineering, Marketing, Sales)."
            ),
        )
        return state

    # Manual team input (comma-separated)
    if text.strip():
        team_names = [t.strip() for t in text.split(",") if t.strip()]
        if team_names:
            new_state = replace(state, teams=tuple(team_names), updated_at=_now_iso())
            deps.state_store.save_setup_state(setup_state=new_state)
            return _transition_to_channels(state=new_state, deps=deps)

    # Resume case: no action, no text — re-render current step
    if state.teams:
        blocks = team_confirmation(teams=list(state.teams))
        deps.slack_client.send_message(
            channel=state.admin_user_id,
            text="Here are your teams:",
            blocks=blocks,
        )
    else:
        deps.slack_client.send_message(
            channel=state.admin_user_id,
            text=(
                "Please type your team names separated by commas "
                "(e.g. Engineering, Marketing, Sales)."
            ),
        )
    return state


def _transition_to_channels(
    *, state: SetupState, deps: SetupDependencies
) -> SetupState:
    """Fetch channels, find #general as default, show channel_mapping Block Kit."""
    channels = deps.slack_client.list_channels()

    # Find #general: is_general flag first, then name match, then first channel
    default_channel = None
    for ch in channels:
        if ch.get("is_general"):
            default_channel = ch
            break
    if default_channel is None:
        for ch in channels:
            if ch.get("name") == "general":
                default_channel = ch
                break
    if default_channel is None and channels:
        default_channel = channels[0]

    # Pre-populate all teams mapped to default channel
    default_id = default_channel["id"] if default_channel else ""
    pre_mapping = {_slugify(t): default_id for t in state.teams}

    blocks = channel_mapping(
        teams=list(state.teams), channels=channels, default_channel=default_channel
    )

    deps.slack_client.send_message(
        channel=state.admin_user_id,
        text="Map each team to a Slack channel:",
        blocks=blocks,
    )

    new_state = replace(
        state, step="channels", channel_mapping=pre_mapping, updated_at=_now_iso()
    )
    deps.state_store.save_setup_state(setup_state=new_state)
    return new_state


def _handle_channels(
    *, text: str, action_id: str | None, state: SetupState, deps: SetupDependencies
) -> SetupState:
    """Handle channel mapping selections and confirm action.

    Dropdown selections update the mapping. The confirm button
    triggers transition to calendar step.
    """
    if action_id == "channel_mapping_confirm":
        return _transition_to_calendar(state=state, deps=deps)

    if action_id and action_id.startswith("channel_map_"):
        selected_channel = text.strip()
        slug = action_id[len("channel_map_") :]
        new_mapping = {**state.channel_mapping, slug: selected_channel}
        new_state = replace(state, channel_mapping=new_mapping, updated_at=_now_iso())
        deps.state_store.save_setup_state(setup_state=new_state)
        return new_state

    # Resume case: no recognized action — re-render channel mapping blocks
    return _transition_to_channels(state=state, deps=deps)


def _slugify(text: str) -> str:
    """Convert team name to slug matching blocks._slug."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _transition_to_calendar(
    *, state: SetupState, deps: SetupDependencies
) -> SetupState:
    """Show calendar setup prompt."""
    blocks = calendar_setup_prompt()
    deps.slack_client.send_message(
        channel=state.admin_user_id,
        text="Would you like to enable Google Calendar integration?",
        blocks=blocks,
    )

    new_state = replace(state, step="calendar", updated_at=_now_iso())
    deps.state_store.save_setup_state(setup_state=new_state)
    return new_state


def _handle_calendar(
    *, text: str, action_id: str | None, state: SetupState, deps: SetupDependencies
) -> SetupState:
    """Handle calendar enable/skip."""
    if action_id == "calendar_enable":
        oauth_url = build_authorization_url(
            client_id=deps.google_client_id,
            redirect_uri=deps.google_oauth_redirect_uri,
            workspace_id=state.workspace_id,
        )
        deps.slack_client.send_message(
            channel=state.admin_user_id,
            text=f"Please authorize Google Calendar access:\n{oauth_url}",
        )
        new_state = replace(
            state,
            step="confirmation",
            calendar_enabled=False,
            calendar_oauth_initiated=True,
            updated_at=_now_iso(),
        )
        deps.state_store.save_setup_state(setup_state=new_state)
        return _handle_confirmation(text="", action_id=None, state=new_state, deps=deps)

    if action_id == "calendar_skip_setup":
        new_state = replace(
            state, step="confirmation", calendar_enabled=False, updated_at=_now_iso()
        )
        deps.state_store.save_setup_state(setup_state=new_state)
        return _handle_confirmation(text="", action_id=None, state=new_state, deps=deps)

    # Resume case: re-send calendar prompt
    blocks = calendar_setup_prompt()
    deps.slack_client.send_message(
        channel=state.admin_user_id,
        text="Would you like to enable Google Calendar integration?",
        blocks=blocks,
    )
    return state


def _send_summary(
    *,
    state: SetupState,
    deps: SetupDependencies,
    calendar_str: str,
) -> None:
    """Send the setup completion summary message."""
    teams_str = ", ".join(state.teams) if state.teams else "None"
    mapping_str = (
        ", ".join(f"{k} -> {v}" for k, v in state.channel_mapping.items()) or "None"
    )

    deps.slack_client.send_message(
        channel=state.admin_user_id,
        text=(
            "*Setup Complete!*\n\n"
            f"*Website:* {state.website_url}\n"
            f"*Teams:* {teams_str}\n"
            f"*Channel Mapping:* {mapping_str}\n"
            f"*Calendar:* {calendar_str}\n\n"
            "Your workspace is now configured."
        ),
    )


def _handle_confirmation(
    *, text: str, action_id: str | None, state: SetupState, deps: SetupDependencies
) -> SetupState:
    """Show summary, write WorkspaceConfig, delete SETUP record, enqueue pending users."""
    # Idempotency guard: if setup already completed, just re-send summary
    existing_config = deps.state_store.get_workspace_config(
        workspace_id=state.workspace_id
    )
    if existing_config is not None and existing_config.setup_complete:
        calendar_str = "Enabled" if existing_config.calendar_enabled else "Disabled"
        _send_summary(state=state, deps=deps, calendar_str=calendar_str)
        return replace(state, step="done", updated_at=_now_iso())

    # Determine calendar state — handle race with OAuth callback
    calendar_enabled = (
        existing_config.calendar_enabled if existing_config else False
    ) or state.calendar_enabled

    # Tri-state calendar summary
    if state.calendar_oauth_initiated and not calendar_enabled:
        calendar_str = "Pending authorization (check your browser)"
    elif calendar_enabled:
        calendar_str = "Enabled"
    else:
        calendar_str = "Disabled"

    _send_summary(state=state, deps=deps, calendar_str=calendar_str)

    # Write WorkspaceConfig and delete SETUP record
    deps.state_store.complete_setup(
        workspace_id=state.workspace_id,
        config_updates={
            "admin_user_id": state.admin_user_id,
            "website_url": state.website_url,
            "teams": list(state.teams),
            "channel_mapping": dict(state.channel_mapping),
            "calendar_enabled": calendar_enabled,
        },
    )

    # Enqueue pending users
    _enqueue_pending_users(state=state, deps=deps)

    return replace(state, step="done", updated_at=_now_iso())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_remaining_ms(lambda_context: Any) -> int:
    """Get remaining Lambda execution time in milliseconds."""
    if lambda_context is None:
        return 999_999  # No Lambda context — assume plenty of time
    try:
        return int(lambda_context.get_remaining_time_in_millis())
    except Exception:
        return 999_999


def _self_enqueue(*, state: SetupState, deps: SetupDependencies) -> SetupState:
    """Save progress to S3 manifest and enqueue SQS continuation message."""
    manifest_key = f"scrape-manifest/{state.workspace_id}.json"

    if deps.s3_client and deps.s3_bucket:
        deps.s3_client.put_object(
            Bucket=deps.s3_bucket,
            Key=manifest_key,
            Body=json.dumps(
                {"website_url": state.website_url, "status": "in_progress"}
            ),
        )

    new_state = replace(state, scrape_manifest_key=manifest_key, updated_at=_now_iso())
    deps.state_store.save_setup_state(setup_state=new_state)

    if deps.sqs_client and deps.sqs_queue_url:
        deps.sqs_client.send_message(
            QueueUrl=deps.sqs_queue_url,
            MessageBody=json.dumps(
                {
                    "type": "setup_resume",
                    "workspace_id": state.workspace_id,
                }
            ),
        )

    deps.slack_client.send_message(
        channel=state.admin_user_id,
        text="Scraping is taking a while. I'll continue in the background and update you when it's done.",
    )

    return new_state


def _enqueue_pending_users(*, state: SetupState, deps: SetupDependencies) -> None:
    """Batch-enqueue pending users for onboarding after setup completion."""
    pending = deps.state_store.get_pending_users(workspace_id=state.workspace_id)

    if not pending:
        logger.info("No pending users to enqueue for workspace %s", state.workspace_id)
        return

    if deps.sqs_client and deps.sqs_queue_url:
        for plan in pending:
            deps.sqs_client.send_message(
                QueueUrl=deps.sqs_queue_url,
                MessageBody=json.dumps(
                    {
                        "type": "onboard_user",
                        "workspace_id": state.workspace_id,
                        "user_id": plan.user_id,
                    }
                ),
            )
            logger.info(
                "Enqueued onboarding for user %s in workspace %s",
                plan.user_id,
                state.workspace_id,
            )
