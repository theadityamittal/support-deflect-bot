"""Slash command handlers for Slack.

/sherpa-status -- show onboarding progress
/sherpa-help -- list available commands
/sherpa-restart -- confirm and restart onboarding
/sherpa-setup -- admin workspace setup
/sherpa-calendar -- admin calendar configuration
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from slack.blocks import calendar_setup_prompt, relink_calendar
from slack.models import EventType, SlackCommand, SQSMessage
from state.models import SetupState, StepStatus

if TYPE_CHECKING:
    from state.dynamo import DynamoStateStore

logger = logging.getLogger(__name__)


def handle_command(
    command: SlackCommand,
    *,
    state_store: DynamoStateStore,
) -> dict[str, Any]:
    handlers = {
        "/sherpa-status": _handle_status,
        "/sherpa-help": _handle_help,
        "/sherpa-restart": _handle_restart,
        "/sherpa-setup": _handle_setup,
        "/sherpa-calendar": _handle_calendar,
    }
    handler = handlers.get(command.command, _handle_unknown)
    return handler(command, state_store=state_store)


def _handle_status(
    command: SlackCommand,
    *,
    state_store: DynamoStateStore,
) -> dict[str, Any]:
    plan = state_store.get_plan(
        workspace_id=command.workspace_id,
        user_id=command.user_id,
    )
    if plan is None:
        return _response("You have no active onboarding plan.")

    status_icons = {
        StepStatus.COMPLETED: "✅",
        StepStatus.IN_PROGRESS: "🔄",
        StepStatus.PENDING: "⬜",
        StepStatus.BLOCKED: "⏸️",
    }
    completed = sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED)
    total = len(plan.steps)
    lines = [f"*Onboarding Progress — {plan.user_name}*\n"]
    for step in plan.steps:
        icon = status_icons.get(step.status, "⬜")
        lines.append(f"{icon} {step.title}")
    pct = int(completed / total * 100) if total else 0
    lines.append(f"\nProgress: {completed}/{total} steps ({pct}%)")
    return _response("\n".join(lines))


def _handle_help(
    command: SlackCommand,
    *,
    state_store: DynamoStateStore,
) -> dict[str, Any]:
    text = (
        "*Sherpa Commands*\n"
        "• `/sherpa-status` — View your onboarding progress\n"
        "• `/sherpa-help` — Show this help message\n"
        "• `/sherpa-restart` — Restart your onboarding (with confirmation)"
    )
    return _response(text)


def _handle_restart(
    command: SlackCommand,
    *,
    state_store: DynamoStateStore,
) -> dict[str, Any]:
    text = (
        "⚠️ *Are you sure you want to restart onboarding?*\n"
        "Your current progress will be reset. "
        "Reply with `confirm restart` to proceed."
    )
    return _response(text)


def _handle_setup(
    command: SlackCommand,
    *,
    state_store: DynamoStateStore,
) -> dict[str, Any]:
    config = state_store.get_workspace_config(workspace_id=command.workspace_id)

    # No CONFIG = first-time setup; create minimal CONFIG and claim admin
    if config is None:
        state_store.save_workspace_config(
            workspace_id=command.workspace_id,
            team_name="",
            bot_user_id="",
            admin_user_id=command.user_id,
        )
    elif not config.admin_user_id:
        # CONFIG exists but no admin — first user claims admin
        state_store.update_workspace_config(
            workspace_id=command.workspace_id,
            updates={"admin_user_id": command.user_id},
        )
    elif command.user_id != config.admin_user_id:
        return _response("Only the workspace admin can run setup.")

    # Check for active setup
    setup = state_store.get_setup_state(workspace_id=command.workspace_id)
    if setup is not None:
        _enqueue_setup_resume(command)
        return _response(f"Resuming setup from step: {setup.step}")

    # Setup already complete — show config
    if config is not None and config.setup_complete:
        lines = [
            "*Workspace Configuration*",
            f"• Team: {config.team_name}",
            f"• Website: {config.website_url or '(not set)'}",
            f"• Teams: {', '.join(config.teams) if config.teams else '(none)'}",
            f"• Calendar enabled: {'Yes' if config.calendar_enabled else 'No'}",
        ]
        return _response("\n".join(lines))

    # Start fresh setup
    now = datetime.now(UTC).isoformat()
    initial_setup = SetupState(
        step="welcome",
        admin_user_id=command.user_id,
        workspace_id=command.workspace_id,
        created_at=now,
        updated_at=now,
    )
    state_store.save_setup_state(setup_state=initial_setup)
    _enqueue_setup_resume(command)
    return _response("Starting workspace setup...")


def _handle_calendar(
    command: SlackCommand,
    *,
    state_store: DynamoStateStore,
) -> dict[str, Any]:
    config = state_store.get_workspace_config(workspace_id=command.workspace_id)
    if (
        config is not None
        and config.admin_user_id
        and command.user_id != config.admin_user_id
    ):
        return _response("Only the workspace admin can run setup.")

    if config is None or not config.setup_complete:
        return _response("Workspace setup is not complete. Run `/sherpa-setup` first.")

    if config.calendar_enabled:
        blocks = relink_calendar(current_email="(linked account)")
        return _blocks_response(blocks)

    blocks = calendar_setup_prompt()
    return _blocks_response(blocks)


def _handle_unknown(
    command: SlackCommand,
    *,
    state_store: DynamoStateStore,
) -> dict[str, Any]:
    return _response(
        f"Unknown command: `{command.command}`. "
        "Try `/sherpa-help` for available commands."
    )


def _enqueue_setup_resume(command: SlackCommand) -> None:
    """Enqueue a synthetic message so the worker re-renders the current setup step."""
    from slack.handler import _enqueue_to_sqs

    timestamp_ms = int(time.time() * 1000)
    msg = SQSMessage(
        version="1.0",
        event_id=f"setup_resume:{command.workspace_id}:{command.user_id}:{timestamp_ms}",
        workspace_id=command.workspace_id,
        user_id=command.user_id,
        channel_id=command.channel_id,
        event_type=EventType.MESSAGE,
        text="",
        timestamp=datetime.now(UTC).isoformat(),
        is_dm=command.channel_id.startswith("D"),
    )
    _enqueue_to_sqs(msg)


def _response(text: str) -> dict[str, Any]:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"response_type": "ephemeral", "text": text}),
    }


def _blocks_response(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"response_type": "ephemeral", "blocks": blocks}),
    }
