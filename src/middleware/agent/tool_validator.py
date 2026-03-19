"""Validate tool calls before execution."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str = ""


def validate_tool_call(
    *, tool_name: str, params: dict[str, Any], available_tools: set[str]
) -> ValidationResult:
    """Validate a tool call before execution."""
    if not tool_name:
        return ValidationResult(valid=False, reason="Empty tool name")

    if tool_name not in available_tools:
        logger.warning("Unknown tool requested: %s", tool_name)
        return ValidationResult(valid=False, reason=f"Unknown tool: {tool_name}")

    return ValidationResult(valid=True)
