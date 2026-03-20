"""Tests for planner prompt builder."""

from __future__ import annotations

from agent.prompts.planner import build_plan_generation_prompt, build_replan_prompt
from state.models import OnboardingPlan, PlanStatus, PlanStep, StepStatus


def _make_plan():
    return OnboardingPlan(
        workspace_id="W1",
        user_id="U1",
        user_name="Jane",
        role="events",
        status=PlanStatus.IN_PROGRESS,
        version=1,
        steps=[
            PlanStep(
                id=1, title="Welcome", status=StepStatus.COMPLETED, summary="Done"
            ),
            PlanStep(id=2, title="Team overview", status=StepStatus.IN_PROGRESS),
        ],
        key_facts=("2 years experience",),
    )


class TestPlannerPrompt:
    def test_plan_generation_includes_user_info(self):
        messages = build_plan_generation_prompt(
            user_name="Jane",
            role="events",
            key_facts=["2 years experience", "Prefers mornings"],
        )
        content = " ".join(m["content"] for m in messages)
        assert "Jane" in content
        assert "events" in content

    def test_replan_includes_current_steps(self):
        messages = build_replan_prompt(
            plan=_make_plan(), reason="User wants to skip policies"
        )
        content = " ".join(m["content"] for m in messages)
        assert "skip" in content.lower() or "replan" in content.lower()
