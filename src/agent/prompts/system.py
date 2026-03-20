"""System prompt builder — plan-anchored context for every LLM call."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from state.models import OnboardingPlan

CALENDAR_INSTRUCTIONS = """
## Calendar Integration
You have access to the calendar_event tool to schedule meetings and events.
- Use it to schedule orientations, training sessions, and check-ins
- Always confirm event details (title, date, time, duration) with the volunteer before creating
- After creating an event, use send_message with blocks_type='calendar_confirmation' to show the details
"""

SYSTEM_BASE = """You are Onboard Assist, a friendly onboarding bot for nonprofit volunteers.

Your job:
- Guide new volunteers through their personalized onboarding plan
- Answer questions using the organization's knowledge base
- Take actions: assign Slack channels, schedule calendar events
- Track progress and adapt when plans need to change

Rules:
- Be warm, concise, and helpful
- Only answer from the knowledge base — never make up information
- If unsure, say so and offer to find out
- One step at a time — don't overwhelm the volunteer
- Use the volunteer's name when natural"""

INTAKE_CONTEXT = """No onboarding plan exists yet for this user.

Your task: Ask 1-3 conversational questions to understand:
1. What role/team they're joining
2. Their experience level (new to nonprofits? returning volunteer?)
3. Any preferences (schedule, learning style)

If their first message already provides this info, you can skip to plan generation.
Respond warmly and ask your first question."""


def build_system_context(
    *,
    plan: OnboardingPlan | None,
    user_message: str,
    calendar_enabled: bool = False,
) -> list[dict[str, Any]]:
    """Build the message list for a reasoning call.

    Args:
        plan: The volunteer's current onboarding plan, or None for intake.
        user_message: The latest message from the volunteer.
        calendar_enabled: When True, calendar instructions are injected into
            the system prompt and calendar_event is listed as an available tool.
    """
    if plan is None:
        base_content = f"{SYSTEM_BASE}\n\n{INTAKE_CONTEXT}"
        if calendar_enabled:
            base_content += CALENDAR_INSTRUCTIONS
        return [
            {"role": "system", "content": base_content},
            {"role": "user", "content": user_message},
        ]

    plan_summary = _format_plan(plan)
    facts = (
        "\n".join(f"- {f}" for f in plan.key_facts) if plan.key_facts else "None yet"
    )

    available_tools = "search_kb, send_message, assign_channel, manage_progress"
    if calendar_enabled:
        available_tools += ", calendar_event"

    system_content = (
        f"{SYSTEM_BASE}\n\n"
        f"## Current Plan (v{plan.version})\n{plan_summary}\n\n"
        f"## Key Facts\n{facts}\n\n"
        f"## Instructions\n"
        f"Look at the current step (in_progress) and decide what to do.\n"
        f"You can use tools: {available_tools}."
    )
    if calendar_enabled:
        system_content += CALENDAR_INSTRUCTIONS

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]

    # Add recent conversation (last 5 messages)
    for msg in plan.recent_messages[-5:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_message})
    return messages


def _format_plan(plan: OnboardingPlan) -> str:
    status_icons = {
        "completed": "✅",
        "in_progress": "🔄",
        "pending": "⬜",
        "blocked": "⏸️",
    }
    lines = [f"Volunteer: {plan.user_name} | Role: {plan.role}"]
    for step in plan.steps:
        icon = status_icons.get(step.status.value, "⬜")
        line = f"{icon} {step.id}. {step.title}"
        if step.summary:
            line += f" — {step.summary}"
        lines.append(line)
    return "\n".join(lines)
