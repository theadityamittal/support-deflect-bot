"""calendar_event tool — STUB for Phase 3.

Logs the request and returns success. Full Google Calendar
implementation will be added in Phase 4 after OAuth setup.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.tools.base import AgentTool, ToolResult

logger = logging.getLogger(__name__)


class CalendarEventTool(AgentTool):
    """Create a Google Calendar event. STUB — returns success without creating."""

    @property
    def name(self) -> str:
        return "calendar_event"

    @property
    def description(self) -> str:
        return "Schedule a Google Calendar event (orientation, training, etc.)."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "date": {"type": "string", "description": "Date (YYYY-MM-DD)"},
                "time": {"type": "string", "description": "Time (HH:MM)"},
                "duration_minutes": {"type": "integer", "description": "Duration"},
                "attendee_email": {
                    "type": "string",
                    "description": "Optional attendee",
                },
            },
            "required": ["title", "date", "time", "duration_minutes"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        title = kwargs.get("title", "")
        logger.info(
            "calendar_event STUB: would create '%s' on %s at %s (%dm)",
            title,
            kwargs.get("date"),
            kwargs.get("time"),
            kwargs.get("duration_minutes"),
        )
        return ToolResult.success(
            data={
                "stubbed": True,
                "title": title,
                "message": "Calendar event scheduled (stub)",
            }
        )
