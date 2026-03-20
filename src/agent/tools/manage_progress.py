"""manage_progress tool — reads/updates the onboarding plan."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agent.tools.base import AgentTool, ToolResult
from state.models import PlanStatus, PlanStep, StepStatus

if TYPE_CHECKING:
    from llm.router import LLMRouter
    from state.dynamo import DynamoStateStore

logger = logging.getLogger(__name__)


class ManageProgressTool(AgentTool):
    """Read and update the onboarding plan, add key facts, trigger replan."""

    def __init__(
        self,
        *,
        state_store: DynamoStateStore,
        workspace_id: str,
        user_id: str,
        router: LLMRouter | None = None,
    ) -> None:
        self._store = state_store
        self._workspace_id = workspace_id
        self._user_id = user_id
        self._router = router

    @property
    def name(self) -> str:
        return "manage_progress"

    @property
    def description(self) -> str:
        return "Read or update the onboarding plan. Actions: get_plan, complete_step, start_step, add_fact, replan."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "get_plan",
                        "complete_step",
                        "start_step",
                        "add_fact",
                        "replan",
                    ],
                },
                "step_id": {
                    "type": "integer",
                    "description": "Step ID (for step actions)",
                },
                "summary": {"type": "string", "description": "Step completion summary"},
                "fact": {"type": "string", "description": "Key fact to remember"},
                "reason": {"type": "string", "description": "Reason for replanning"},
            },
            "required": ["action"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action", "")
        handlers = {
            "get_plan": self._get_plan,
            "complete_step": self._complete_step,
            "start_step": self._start_step,
            "add_fact": self._add_fact,
            "replan": self._replan,
        }
        handler = handlers.get(action)
        if not handler:
            return ToolResult.failure(error=f"Unknown action: {action}")
        return handler(**kwargs)

    def _get_plan(self, **_kwargs: Any) -> ToolResult:
        plan = self._store.get_plan(
            workspace_id=self._workspace_id, user_id=self._user_id
        )
        if not plan:
            return ToolResult.success(data={"plan": None})
        return ToolResult.success(data={"plan": _plan_to_dict(plan)})

    def _complete_step(self, **kwargs: Any) -> ToolResult:
        plan = self._store.get_plan(
            workspace_id=self._workspace_id, user_id=self._user_id
        )
        if not plan:
            return ToolResult.failure(error="No active plan found")

        step_id = kwargs.get("step_id")
        summary = kwargs.get("summary", "")
        now = datetime.now(UTC)

        new_steps = []
        for step in plan.steps:
            if step.id == step_id:
                new_steps.append(
                    replace(
                        step,
                        status=StepStatus.COMPLETED,
                        completed_at=now,
                        summary=summary,
                    )
                )
            else:
                new_steps.append(step)

        updated = replace(plan, steps=new_steps, updated_at=now)
        self._store.save_plan(updated)
        self._check_plan_completion(updated)
        return ToolResult.success(data={"step_id": step_id, "status": "completed"})

    def _start_step(self, **kwargs: Any) -> ToolResult:
        plan = self._store.get_plan(
            workspace_id=self._workspace_id, user_id=self._user_id
        )
        if not plan:
            return ToolResult.failure(error="No active plan found")

        step_id = kwargs.get("step_id")
        now = datetime.now(UTC)

        new_steps = []
        for step in plan.steps:
            if step.id == step_id:
                new_steps.append(
                    replace(step, status=StepStatus.IN_PROGRESS, started_at=now)
                )
            else:
                new_steps.append(step)

        updated = replace(plan, steps=new_steps, updated_at=now)
        self._store.save_plan(updated)
        return ToolResult.success(data={"step_id": step_id, "status": "in_progress"})

    def _add_fact(self, **kwargs: Any) -> ToolResult:
        plan = self._store.get_plan(
            workspace_id=self._workspace_id, user_id=self._user_id
        )
        if not plan:
            return ToolResult.failure(error="No active plan found")

        fact = kwargs.get("fact", "")
        new_facts = plan.key_facts + (fact,)
        updated = replace(plan, key_facts=new_facts, updated_at=datetime.now(UTC))
        self._store.save_plan(updated)
        return ToolResult.success(data={"fact": fact, "total_facts": len(new_facts)})

    def _replan(self, **kwargs: Any) -> ToolResult:
        plan = self._store.get_plan(
            workspace_id=self._workspace_id, user_id=self._user_id
        )
        if not plan:
            return ToolResult.failure(error="No active plan found")
        if not self._router:
            return ToolResult.failure(error="Router not available for replanning")

        from agent.prompts.planner import build_replan_prompt
        from llm.provider import ModelRole

        reason = kwargs.get("reason", "User requested changes")
        messages = build_replan_prompt(plan=plan, reason=reason)
        response = self._router.invoke(role=ModelRole.REASONING, messages=messages)

        import json as _json

        try:
            new_steps_raw = _json.loads(response.text)
        except _json.JSONDecodeError:
            return ToolResult.failure(error="Failed to parse replan response")

        new_steps = [
            PlanStep(
                id=s["id"],
                title=s["title"],
                status=StepStatus(s.get("status", "pending")),
            )
            for s in new_steps_raw
        ]
        updated = replace(
            plan,
            steps=new_steps,
            version=plan.version + 1,
            updated_at=datetime.now(UTC),
        )
        self._store.save_plan(updated)
        return ToolResult.success(
            data={"version": updated.version, "steps": len(new_steps)}
        )

    def _check_plan_completion(self, plan: Any) -> None:
        """If all steps completed, save a CompletionRecord."""
        from state.models import CompletionRecord

        all_done = all(s.status == StepStatus.COMPLETED for s in plan.steps)
        if not all_done:
            return

        now = datetime.now(UTC)
        duration = int((now - plan.created_at).total_seconds() / 60)
        channels = [s.title for s in plan.steps if s.requires_tool == "assign_channel"]

        record = CompletionRecord(
            workspace_id=self._workspace_id,
            user_id=self._user_id,
            role=plan.role,
            plan_version=plan.version,
            steps_completed=len(plan.steps),
            replans=plan.version - 1,
            duration_minutes=duration,
            channels_assigned=tuple(channels),
            calendar_events_created=0,
        )
        self._store.save_completion_record(record)

        completed_plan = replace(plan, status=PlanStatus.COMPLETED, updated_at=now)
        self._store.save_plan(completed_plan)
        logger.info("Plan completed for %s/%s", self._workspace_id, self._user_id)


def _plan_to_dict(plan: Any) -> dict[str, Any]:
    """Convert plan to a compact dict for LLM context."""
    return {
        "version": plan.version,
        "status": plan.status.value,
        "role": plan.role,
        "steps": [
            {
                "id": s.id,
                "title": s.title,
                "status": s.status.value,
                **({"summary": s.summary} if s.summary else {}),
                **({"requires_tool": s.requires_tool} if s.requires_tool else {}),
            }
            for s in plan.steps
        ],
        "key_facts": list(plan.key_facts),
    }
