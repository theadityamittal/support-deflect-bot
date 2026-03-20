"""Tests for responder prompt builder."""

from __future__ import annotations

from agent.prompts.responder import build_response_prompt
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
    )


class TestResponderPrompt:
    def test_includes_tool_results(self):
        messages = build_response_prompt(
            plan=_make_plan(),
            user_message="What does the events team do?",
            tool_results=[
                {
                    "tool": "search_kb",
                    "data": {"results": [{"text": "Events team runs fundraisers"}]},
                }
            ],
        )
        content = " ".join(m["content"] for m in messages)
        assert "fundraisers" in content.lower() or "search_kb" in content.lower()
