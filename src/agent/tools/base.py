"""Abstract tool interface for agent tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    """Immutable result from a tool execution."""

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def success(cls, *, data: dict[str, Any]) -> ToolResult:
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, *, error: str) -> ToolResult:
        return cls(ok=False, error=error)


class AgentTool(ABC):
    """Base class for all agent tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in LLM tool-calling."""

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description for the LLM."""

    @property
    @abstractmethod
    def parameters_schema(self) -> dict[str, Any]:
        """JSON Schema for tool parameters."""

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with validated parameters."""
