"""Tests for manage_progress agent tool."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent.tools.manage_progress import ManageProgressTool
from state.models import OnboardingPlan, PlanStatus, PlanStep, StepStatus


def _make_plan(**overrides):
    defaults = {
        "workspace_id": "W1",
        "user_id": "U1",
        "user_name": "Jane",
        "role": "events",
        "status": PlanStatus.IN_PROGRESS,
        "version": 1,
        "steps": [
            PlanStep(
                id=1, title="Welcome", status=StepStatus.COMPLETED, summary="Done"
            ),
            PlanStep(id=2, title="Team overview", status=StepStatus.IN_PROGRESS),
            PlanStep(id=3, title="Policies", status=StepStatus.PENDING),
        ],
        "key_facts": ("Likes events",),
    }
    defaults.update(overrides)
    return OnboardingPlan(**defaults)


class TestManageProgressTool:
    def test_name(self):
        tool = ManageProgressTool(
            state_store=MagicMock(), workspace_id="W1", user_id="U1"
        )
        assert tool.name == "manage_progress"

    def test_get_plan(self):
        mock_store = MagicMock()
        plan = _make_plan()
        mock_store.get_plan.return_value = plan
        tool = ManageProgressTool(
            state_store=mock_store, workspace_id="W1", user_id="U1"
        )

        result = tool.execute(action="get_plan")

        assert result.ok is True
        assert result.data["plan"]["version"] == 1
        assert len(result.data["plan"]["steps"]) == 3

    def test_complete_step(self):
        mock_store = MagicMock()
        mock_store.get_plan.return_value = _make_plan()
        tool = ManageProgressTool(
            state_store=mock_store, workspace_id="W1", user_id="U1"
        )

        result = tool.execute(
            action="complete_step", step_id=2, summary="Covered team structure"
        )

        assert result.ok is True
        saved_plan = mock_store.save_plan.call_args[0][0]
        step_2 = [s for s in saved_plan.steps if s.id == 2][0]
        assert step_2.status == StepStatus.COMPLETED
        assert step_2.summary == "Covered team structure"

    def test_add_fact(self):
        mock_store = MagicMock()
        mock_store.get_plan.return_value = _make_plan()
        tool = ManageProgressTool(
            state_store=mock_store, workspace_id="W1", user_id="U1"
        )

        result = tool.execute(action="add_fact", fact="Prefers morning meetings")

        assert result.ok is True
        saved_plan = mock_store.save_plan.call_args[0][0]
        assert "Prefers morning meetings" in saved_plan.key_facts

    def test_replan(self):
        mock_store = MagicMock()
        mock_store.get_plan.return_value = _make_plan()
        mock_router = MagicMock()
        mock_router.invoke.return_value = MagicMock(
            text='[{"id":1,"title":"Welcome","status":"completed"},{"id":2,"title":"Team overview","status":"in_progress"},{"id":3,"title":"New step","status":"pending"}]',
            output_tokens=50,
        )
        tool = ManageProgressTool(
            state_store=mock_store,
            workspace_id="W1",
            user_id="U1",
            router=mock_router,
        )

        result = tool.execute(action="replan", reason="User wants different path")

        assert result.ok is True
        saved_plan = mock_store.save_plan.call_args[0][0]
        assert saved_plan.version == 2

    def test_complete_step_no_plan(self):
        mock_store = MagicMock()
        mock_store.get_plan.return_value = None
        tool = ManageProgressTool(
            state_store=mock_store, workspace_id="W1", user_id="U1"
        )

        result = tool.execute(action="complete_step", step_id=1)

        assert result.ok is False
        assert "no active plan" in result.error.lower()
