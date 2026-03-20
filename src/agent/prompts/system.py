"""System prompt builder — plan-anchored context for every LLM call."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from state.models import OnboardingPlan

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
    *, plan: OnboardingPlan | None, user_message: str
) -> list[dict[str, Any]]:
    """Build the message list for a reasoning call."""
    if plan is None:
        return [
            {"role": "system", "content": f"{SYSTEM_BASE}\n\n{INTAKE_CONTEXT}"},
            {"role": "user", "content": user_message},
        ]

    plan_summary = _format_plan(plan)
    facts = (
        "\n".join(f"- {f}" for f in plan.key_facts) if plan.key_facts else "None yet"
    )

    system_content = (
        f"{SYSTEM_BASE}\n\n"
        f"## Current Plan (v{plan.version})\n{plan_summary}\n\n"
        f"## Key Facts\n{facts}\n\n"
        f"## Instructions\n"
        f"Look at the current step (in_progress) and decide what to do.\n"
        f"You can use tools: search_kb, send_message, assign_channel, "
        f"calendar_event, manage_progress."
    )

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
