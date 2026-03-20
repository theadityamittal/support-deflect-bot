"""Prompts for plan generation and replanning."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from state.models import OnboardingPlan


def build_plan_generation_prompt(
    *, user_name: str, role: str, key_facts: list[str]
) -> list[dict[str, Any]]:
    """Build prompt for initial plan generation (Nova Micro)."""
    facts_str = "\n".join(f"- {f}" for f in key_facts) if key_facts else "No facts yet"
    return [
        {
            "role": "system",
            "content": (
                "You are a planner for nonprofit volunteer onboarding.\n\n"
                "Generate a personalized onboarding plan as a JSON array of steps.\n"
                'Each step: {"id": N, "title": "...", "requires_tool": "tool_name" or null}\n\n'
                "Available tools for steps: search_kb, assign_channel, calendar_event\n\n"
                "A typical plan has 5-8 steps:\n"
                "1. Welcome & org overview (search_kb)\n"
                "2. Role-specific info (search_kb)\n"
                "3. Key policies (search_kb)\n"
                "4. Communication tools setup (assign_channel)\n"
                "5. Schedule orientation (calendar_event)\n"
                "6. Comprehension check & wrap-up\n\n"
                "Adapt based on the volunteer's role and experience.\n"
                "Output ONLY the JSON array, no other text."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Volunteer: {user_name}\n"
                f"Role: {role}\n"
                f"Key facts:\n{facts_str}\n\n"
                f"Generate their onboarding plan."
            ),
        },
    ]


def build_replan_prompt(*, plan: OnboardingPlan, reason: str) -> list[dict[str, Any]]:
    """Build prompt for incremental replanning (Nova Micro)."""
    steps_str = "\n".join(f"  {s.id}. [{s.status.value}] {s.title}" for s in plan.steps)
    return [
        {
            "role": "system",
            "content": (
                "You are replanning an onboarding sequence.\n\n"
                "Rules:\n"
                "- ONLY modify pending steps\n"
                "- completed and in_progress steps are FROZEN — do not change them\n"
                "- You can insert, remove, or reorder pending steps\n"
                "- Output the FULL step list as a JSON array\n"
                "- Keep step IDs sequential\n"
                "- Output ONLY the JSON array, no other text."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Current plan (v{plan.version}):\n{steps_str}\n\n"
                f"Reason for replan: {reason}\n\n"
                f"Output the updated step list."
            ),
        },
    ]
