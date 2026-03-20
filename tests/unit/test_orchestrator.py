"""Tests for the agent orchestrator."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent.orchestrator import Orchestrator
from llm.provider import LLMResponse
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


class TestOrchestrator:
    def test_simple_turn_no_tools(self):
        """Reasoning says no tools needed, generation produces response."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = [
            LLMResponse(
                text='{"action": "respond", "reasoning": "User said hi, just greet them"}',
                input_tokens=100,
                output_tokens=50,
                model_id="gemini-2.5-flash-lite",
            ),
            LLMResponse(
                text="Hi Jane! How can I help you today?",
                input_tokens=200,
                output_tokens=30,
                model_id="gemini-2.5-flash",
            ),
        ]

        mock_store = MagicMock()
        mock_store.get_plan.return_value = _make_plan()

        orch = Orchestrator(
            router=mock_router,
            state_store=mock_store,
            tools={},
            workspace_id="W1",
            user_id="U1",
            channel_id="C1",
        )

        response = orch.process_turn(user_message="hi")

        assert "Jane" in response or "Hi" in response
        assert mock_router.invoke.call_count == 2

    def test_turn_with_tool_call(self):
        """Reasoning requests a tool, orchestrator executes it, then generates."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = [
            LLMResponse(
                text='{"action": "tool_call", "tool": "search_kb", "params": {"query": "events team"}}',
                input_tokens=100,
                output_tokens=60,
                model_id="gemini-2.5-flash-lite",
            ),
            LLMResponse(
                text='{"action": "respond", "reasoning": "Got KB results, ready to answer"}',
                input_tokens=200,
                output_tokens=40,
                model_id="gemini-2.5-flash-lite",
            ),
            LLMResponse(
                text="The events team handles fundraising galas and community events.",
                input_tokens=300,
                output_tokens=50,
                model_id="gemini-2.5-flash",
            ),
        ]

        mock_tool = MagicMock()
        mock_tool.name = "search_kb"
        mock_tool.execute.return_value = MagicMock(
            ok=True, data={"results": [{"text": "Events team info"}]}
        )

        mock_store = MagicMock()
        mock_store.get_plan.return_value = _make_plan()

        orch = Orchestrator(
            router=mock_router,
            state_store=mock_store,
            tools={"search_kb": mock_tool},
            workspace_id="W1",
            user_id="U1",
            channel_id="C1",
        )

        response = orch.process_turn(user_message="What does the events team do?")

        assert "events" in response.lower()
        mock_tool.execute.assert_called_once()

    def test_no_plan_triggers_intake(self):
        """First interaction — no plan exists, agent asks intake questions."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = [
            LLMResponse(
                text='{"action": "respond", "reasoning": "New user, ask about role"}',
                input_tokens=100,
                output_tokens=40,
                model_id="gemini-2.5-flash-lite",
            ),
            LLMResponse(
                text="Welcome! What team or role will you be helping with?",
                input_tokens=200,
                output_tokens=30,
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
            user_id="U1",
            channel_id="C1",
        )

        response = orch.process_turn(user_message="hi")

        assert (
            "role" in response.lower()
            or "team" in response.lower()
            or "welcome" in response.lower()
        )
