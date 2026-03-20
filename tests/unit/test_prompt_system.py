"""Tests for system prompt builder."""

from __future__ import annotations

from agent.prompts.system import build_system_context
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
        recent_messages=({"role": "user", "content": "Tell me about the events team"},),
    )


class TestSystemContext:
    def test_includes_plan_steps(self):
        messages = build_system_context(plan=_make_plan(), user_message="hi")
        system_msg = messages[0]["content"]
        assert "Welcome" in system_msg
        assert "Team overview" in system_msg

    def test_includes_key_facts(self):
        messages = build_system_context(plan=_make_plan(), user_message="hi")
        system_msg = messages[0]["content"]
        assert "2 years experience" in system_msg

    def test_no_plan_returns_intake_context(self):
        messages = build_system_context(plan=None, user_message="hi I'm new")
        system_msg = messages[0]["content"]
        assert "intake" in system_msg.lower() or "role" in system_msg.lower()
