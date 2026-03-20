"""Integration test: SQS message → orchestrator → tools → response."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from agent.orchestrator import Orchestrator
from agent.tools.base import ToolResult
from llm.provider import LLMResponse
from state.models import OnboardingPlan, PlanStatus, PlanStep, StepStatus


@pytest.mark.integration
class TestAgentPipeline:
    def test_full_turn_with_kb_search(self):
        """Simulate: user asks question → reasoning → search_kb → generation → response."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = [
            LLMResponse(
                text='{"action": "tool_call", "tool": "search_kb", "params": {"query": "events"}}',
                input_tokens=100,
                output_tokens=60,
                model_id="gemini-2.5-flash-lite",
            ),
            LLMResponse(
                text='{"action": "respond"}',
                input_tokens=200,
                output_tokens=30,
                model_id="gemini-2.5-flash-lite",
            ),
            LLMResponse(
                text="The events team organizes community fundraisers and galas.",
                input_tokens=300,
                output_tokens=40,
                model_id="gemini-2.5-flash",
            ),
        ]

        mock_store = MagicMock()
        plan = OnboardingPlan(
            workspace_id="W1",
            user_id="U1",
            user_name="Jane",
            role="events",
            status=PlanStatus.IN_PROGRESS,
            version=1,
            steps=[
                PlanStep(
                    id=1,
                    title="Welcome",
                    status=StepStatus.COMPLETED,
                    summary="Done",
                ),
                PlanStep(
                    id=2,
                    title="Events overview",
                    status=StepStatus.IN_PROGRESS,
                ),
            ],
        )
        mock_store.get_plan.return_value = plan

        mock_search = MagicMock()
        mock_search.name = "search_kb"
        mock_search.execute.return_value = ToolResult.success(
            data={"results": [{"text": "Events team runs galas"}]}
        )

        orch = Orchestrator(
            router=mock_router,
            state_store=mock_store,
            tools={"search_kb": mock_search},
            workspace_id="W1",
            user_id="U1",
            channel_id="C1",
        )

        response = orch.process_turn(user_message="What does the events team do?")

        assert "events" in response.lower() or "fundraisers" in response.lower()
        mock_search.execute.assert_called_once()
        assert mock_router.invoke.call_count == 3  # 2 reasoning + 1 generation
        mock_store.save_plan.assert_called_once()  # Context updated

    def test_new_user_intake_flow(self):
        """First message from new user — no plan, agent asks intake question."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = [
            LLMResponse(
                text='{"action": "respond", "reasoning": "New user, need to ask role"}',
                input_tokens=100,
                output_tokens=40,
                model_id="gemini-2.5-flash-lite",
            ),
            LLMResponse(
                text="Welcome to Changing the Present! What team are you joining?",
                input_tokens=150,
                output_tokens=25,
                model_id="gemini-2.5-flash",
            ),
        ]

        mock_store = MagicMock()
        mock_store.get_plan.return_value = None

        orch = Orchestrator(
            router=mock_router,
            state_store=mock_store,
            tools={},
            workspace_id="W1",
            user_id="U_NEW",
            channel_id="C1",
        )

        response = orch.process_turn(user_message="hi, I'm new here")

        assert len(response) > 0
        # No plan to save since none exists
        mock_store.save_plan.assert_not_called()
