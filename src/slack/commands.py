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
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from slack.blocks import calendar_setup_prompt, relink_calendar
from state.models import SetupState, StepStatus

if TYPE_CHECKING:
    from slack.models import SlackCommand
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
    if (
        config is not None
        and config.admin_user_id
        and command.user_id != config.admin_user_id
    ):
        return _response("Only the workspace admin can run setup.")

    setup = state_store.get_setup_state(workspace_id=command.workspace_id)
    if setup is not None:
        return _response(f"Setup in progress. Resuming from step: {setup.step}")

    if config is not None and config.setup_complete:
        lines = [
            "*Workspace Configuration*",
            f"• Team: {config.team_name}",
            f"• Website: {config.website_url or '(not set)'}",
            f"• Teams: {', '.join(config.teams) if config.teams else '(none)'}",
            f"• Calendar enabled: {'Yes' if config.calendar_enabled else 'No'}",
        ]
        return _response("\n".join(lines))

    now = datetime.now(UTC).isoformat()
    initial_setup = SetupState(
        step="welcome",
        admin_user_id=command.user_id,
        workspace_id=command.workspace_id,
        created_at=now,
        updated_at=now,
    )
    state_store.save_setup_state(setup_state=initial_setup)
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
