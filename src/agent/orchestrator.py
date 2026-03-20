"""Agent orchestrator — reasoning → tool → generation loop."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from agent.prompts.responder import build_response_prompt
from agent.prompts.system import build_system_context
from llm.provider import ModelRole
from middleware.agent.output_validator import validate_output
from middleware.agent.tool_validator import validate_tool_call
from middleware.agent.turn_budget import TurnBudgetEnforcer, TurnBudgetExceededError

if TYPE_CHECKING:
    from agent.tools.base import AgentTool
    from llm.router import LLMRouter
    from state.dynamo import DynamoStateStore
    from state.models import OnboardingPlan

logger = logging.getLogger(__name__)

MAX_REASONING_LOOPS = 5


class Orchestrator:
    """Runs the reasoning → tool → generation loop for one turn."""

    def __init__(
        self,
        *,
        router: LLMRouter,
        state_store: DynamoStateStore,
        tools: dict[str, AgentTool],
        workspace_id: str,
        user_id: str,
        channel_id: str,
        budget: TurnBudgetEnforcer | None = None,
    ) -> None:
        self._router = router
        self._store = state_store
        self._tools = tools
        self._workspace_id = workspace_id
        self._user_id = user_id
        self._channel_id = channel_id
        self._budget = budget or TurnBudgetEnforcer(
            max_reasoning_calls=3,
            max_generation_calls=1,
            max_tool_calls=4,
            max_output_tokens=5000,
        )

    def process_turn(self, *, user_message: str) -> str:
        """Process one user message and return the bot's response text."""
        plan = self._store.get_plan(
            workspace_id=self._workspace_id, user_id=self._user_id
        )

        tool_results: list[dict[str, Any]] = []

        try:
            # Reasoning loop
            for _ in range(MAX_REASONING_LOOPS):
                self._budget.check_reasoning_budget()
                messages = build_system_context(plan=plan, user_message=user_message)

                # Append tool results to context if any
                if tool_results:
                    tool_ctx = "\n".join(
                        f"[{tr['tool']}]: {json.dumps(tr['data'])}"
                        for tr in tool_results
                    )
                    messages.append(
                        {
                            "role": "assistant",
                            "content": f"Tool results:\n{tool_ctx}",
                        }
                    )

                reasoning = self._router.invoke(
                    role=ModelRole.REASONING, messages=messages
                )
                self._budget.record_reasoning_call(
                    output_tokens=reasoning.output_tokens
                )

                decision = _parse_reasoning(reasoning.text)

                if decision["action"] == "respond":
                    break

                if decision["action"] == "tool_call":
                    tool_name = decision.get("tool", "")
                    params = decision.get("params", {})

                    validation = validate_tool_call(
                        tool_name=tool_name,
                        params=params,
                        available_tools=set(self._tools.keys()),
                    )
                    if not validation.valid:
                        logger.warning(
                            "Invalid tool call: %s — %s",
                            tool_name,
                            validation.reason,
                        )
                        continue

                    self._budget.check_tool_budget()
                    tool = self._tools[tool_name]
                    result = tool.execute(**params)
                    self._budget.record_tool_call()

                    tool_results.append(
                        {
                            "tool": tool_name,
                            "ok": result.ok,
                            "data": result.data,
                            "error": result.error,
                        }
                    )

            # Generation phase
            self._budget.check_generation_budget()
            gen_messages = build_response_prompt(
                plan=plan,
                user_message=user_message,
                tool_results=tool_results,
            )
            generation = self._router.invoke(
                role=ModelRole.GENERATION, messages=gen_messages
            )
            self._budget.record_generation_call(output_tokens=generation.output_tokens)

            response_text = validate_output(generation.text)

            # Update recent messages in plan
            self._update_context(
                plan=plan, user_message=user_message, response=response_text
            )

            return str(response_text)

        except TurnBudgetExceededError as e:
            logger.warning("Turn budget exceeded: %s", e)
            return (
                "I've reached my processing limit for this message. "
                "Here's what I have so far — feel free to send another "
                "message to continue."
            )

    def _update_context(
        self,
        *,
        plan: OnboardingPlan | None,
        user_message: str,
        response: str,
    ) -> None:
        """Append the exchange to recent_messages and save."""
        if plan is None:
            return

        from dataclasses import replace
        from datetime import UTC, datetime

        new_messages = list(plan.recent_messages[-4:]) + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": response},
        ]
        updated = replace(
            plan,
            recent_messages=tuple(new_messages),
            updated_at=datetime.now(UTC),
        )
        self._store.save_plan(updated)


def _parse_reasoning(text: str) -> dict[str, Any]:
    """Parse reasoning model output as JSON decision."""
    try:
        result: dict[str, Any] = json.loads(text)
        return result
    except json.JSONDecodeError:
        logger.debug("Reasoning output not JSON, treating as respond: %s", text[:100])
        return {"action": "respond", "reasoning": text}
