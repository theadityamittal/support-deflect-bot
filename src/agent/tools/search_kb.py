"""search_kb tool — queries Pinecone for org knowledge."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agent.tools.base import AgentTool, ToolResult

if TYPE_CHECKING:
    from rag.vectorstore import PineconeVectorStore

logger = logging.getLogger(__name__)


class SearchKBTool(AgentTool):
    """Search the organization's knowledge base via Pinecone."""

    def __init__(self, *, vectorstore: PineconeVectorStore, namespace: str) -> None:
        self._vectorstore = vectorstore
        self._namespace = namespace

    @property
    def name(self) -> str:
        return "search_kb"

    @property
    def description(self) -> str:
        return "Search the organization's knowledge base for relevant information."

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query",
                }
            },
            "required": ["query"],
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query", "")
        try:
            results = self._vectorstore.search(
                query=query, namespace=self._namespace, top_k=5
            )
            return ToolResult.success(
                data={
                    "results": [
                        {"id": r.id, "score": r.score, "text": r.text} for r in results
                    ]
                }
            )
        except Exception as e:
            logger.exception("search_kb failed for query: %s", query)
            return ToolResult.failure(error=f"Knowledge base search failed: {e}")
