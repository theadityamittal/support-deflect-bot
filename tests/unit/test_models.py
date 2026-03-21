"""Tests for DynamoDB state models."""

from datetime import UTC, datetime

import pytest

from state.models import (
    CompletionRecord,
    OnboardingPlan,
    PlanStatus,
    PlanStep,
    StepStatus,
    UsageRecord,
)


class TestPlanStep:
    def test_create_pending_step(self):
        step = PlanStep(id=1, title="Welcome", status=StepStatus.PENDING)
        assert step.id == 1
        assert step.status == StepStatus.PENDING
        assert step.summary is None

    def test_step_is_frozen(self):
        step = PlanStep(id=1, title="Welcome", status=StepStatus.PENDING)
        with pytest.raises(AttributeError):
            step.title = "Changed"

    def test_complete_step(self):
        now = datetime.now(UTC)
        step = PlanStep(
            id=1,
            title="Welcome",
            status=StepStatus.COMPLETED,
            completed_at=now,
            summary="Introduced org",
        )
        assert step.completed_at == now
        assert step.summary == "Introduced org"


class TestOnboardingPlan:
    def test_create_plan(self):
        steps = [
            PlanStep(
                id=1, title="Welcome", status=StepStatus.COMPLETED, summary="Done"
            ),
            PlanStep(id=2, title="Intake", status=StepStatus.IN_PROGRESS),
            PlanStep(id=3, title="Training", status=StepStatus.PENDING),
        ]
        plan = OnboardingPlan(
            workspace_id="W456",
            user_id="U123",
            user_name="Jane",
            role="events",
            status=PlanStatus.IN_PROGRESS,
            version=1,
            steps=steps,
        )
        assert plan.workspace_id == "W456"
        assert len(plan.steps) == 3

    def test_plan_is_frozen(self):
        plan = OnboardingPlan(
            workspace_id="W1",
            user_id="U1",
            user_name="Test",
            role="general",
            status=PlanStatus.IN_PROGRESS,
            version=1,
            steps=[],
        )
        with pytest.raises(AttributeError):
            plan.version = 2

    def test_plan_to_dynamo_item(self):
        steps = [PlanStep(id=1, title="Welcome", status=StepStatus.PENDING)]
        plan = OnboardingPlan(
            workspace_id="W456",
            user_id="U123",
            user_name="Jane",
            role="events",
            status=PlanStatus.IN_PROGRESS,
            version=1,
            steps=steps,
        )
        item = plan.to_dynamo_item()
        assert item["pk"] == "WORKSPACE#W456"
        assert item["sk"] == "PLAN#U123"
        assert "plan" in item
        assert item["plan"]["version"] == 1

    def test_plan_from_dynamo_item(self):
        item = {
            "pk": "WORKSPACE#W456",
            "sk": "PLAN#U123",
            "workspace_id": "W456",
            "user_id": "U123",
            "user_name": "Jane",
            "role": "events",
            "status": "in_progress",
            "plan": {
                "version": 2,
                "steps": [
                    {
                        "id": 1,
                        "title": "Welcome",
                        "status": "completed",
                        "summary": "Done",
                    },
                    {"id": 2, "title": "Intake", "status": "pending"},
                ],
            },
            "context": {"key_facts": ["likes morning meetings"]},
        }
        plan = OnboardingPlan.from_dynamo_item(item)
        assert plan.workspace_id == "W456"
        assert plan.version == 2
        assert len(plan.steps) == 2
        assert plan.steps[0].status == StepStatus.COMPLETED


class TestCompletionRecord:
    def test_create_record(self):
        record = CompletionRecord(
            workspace_id="W456",
            user_id="U123",
            role="events",
            plan_version=3,
            steps_completed=7,
            replans=2,
            duration_minutes=360,
            channels_assigned=("events", "general"),
            calendar_events_created=1,
        )
        assert record.steps_completed == 7
        assert record.replans == 2

    def test_record_is_frozen(self):
        record = CompletionRecord(
            workspace_id="W1",
            user_id="U1",
            role="general",
            plan_version=1,
            steps_completed=3,
            replans=0,
            duration_minutes=60,
            channels_assigned=(),
            calendar_events_created=0,
        )
        with pytest.raises(AttributeError):
            record.replans = 5

    def test_record_to_dynamo_item(self):
        record = CompletionRecord(
            workspace_id="W456",
            user_id="U123",
            role="events",
            plan_version=3,
            steps_completed=7,
            replans=2,
            duration_minutes=360,
            channels_assigned=("events",),
            calendar_events_created=1,
        )
        item = record.to_dynamo_item()
        assert item["pk"] == "WORKSPACE#W456"
        assert item["sk"] == "COMPLETED#U123"
        assert "ttl" not in item  # Completion records never expire


class TestUsageRecord:
    def test_create_daily_usage(self):
        record = UsageRecord(
            workspace_id="W456",
            user_id="U123",
            date="2026-03-18",
            turns=10,
            output_tokens=5000,
            tool_calls=20,
            estimated_cost=0.05,
        )
        assert record.turns == 10
        assert record.estimated_cost == 0.05

    def test_usage_is_frozen(self):
        record = UsageRecord(
            workspace_id="W1",
            user_id="U1",
            date="2026-03-18",
            turns=0,
            output_tokens=0,
            tool_calls=0,
            estimated_cost=0.0,
        )
        with pytest.raises(AttributeError):
            record.turns = 999
