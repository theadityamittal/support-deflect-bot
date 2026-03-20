"""Tests for search_kb agent tool."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent.tools.search_kb import SearchKBTool
from rag.vectorstore import SearchResult


class TestSearchKBTool:
    def test_name(self):
        tool = SearchKBTool(vectorstore=MagicMock(), namespace="test")
        assert tool.name == "search_kb"

    def test_search_returns_results(self):
        mock_vs = MagicMock()
        mock_vs.search.return_value = [
            SearchResult(id="1", score=0.85, text="Volunteer handbook content"),
            SearchResult(id="2", score=0.72, text="Events team info"),
        ]
        tool = SearchKBTool(vectorstore=mock_vs, namespace="ws-123")

        result = tool.execute(query="events team")

        assert result.ok is True
        assert len(result.data["results"]) == 2
        assert result.data["results"][0]["text"] == "Volunteer handbook content"
        mock_vs.search.assert_called_once_with(
            query="events team", namespace="ws-123", top_k=5
        )

    def test_search_no_results(self):
        mock_vs = MagicMock()
        mock_vs.search.return_value = []
        tool = SearchKBTool(vectorstore=mock_vs, namespace="ws-123")

        result = tool.execute(query="nonexistent topic")

        assert result.ok is True
        assert result.data["results"] == []

    def test_search_handles_error(self):
        mock_vs = MagicMock()
        mock_vs.search.side_effect = Exception("Pinecone timeout")
        tool = SearchKBTool(vectorstore=mock_vs, namespace="ws-123")

        result = tool.execute(query="anything")

        assert result.ok is False
        assert "Pinecone timeout" in result.error
