"""Prompt for generating the user-facing response (Claude Haiku)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from state.models import OnboardingPlan


def build_response_prompt(
    *,
    plan: OnboardingPlan | None,
    user_message: str,
    tool_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build prompt for response generation (Haiku)."""
    tool_context = ""
    if tool_results:
        parts = []
        for tr in tool_results:
            parts.append(f"Tool: {tr['tool']}\nResult: {tr['data']}")
        tool_context = "\n\n".join(parts)

    plan_context = ""
    if plan:
        current_step = next(
            (s for s in plan.steps if s.status.value == "in_progress"), None
        )
        if current_step:
            plan_context = f"Current step: {current_step.title}"

    system = (
        "You are Onboard Assist, writing a response to a volunteer.\n\n"
        "Guidelines:\n"
        "- Be warm, concise, and helpful\n"
        "- Use the tool results to inform your answer\n"
        "- If search_kb returned results, synthesize them naturally\n"
        "- Don't mention tools or internal processes\n"
        "- Keep responses under 300 words\n"
        "- Use markdown formatting for Slack (bold, lists, etc.)"
    )

    if plan_context:
        system += f"\n\n{plan_context}"

    if tool_context:
        system += f"\n\n## Tool Results\n{tool_context}"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]
