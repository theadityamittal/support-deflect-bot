"""Tests for RAG pipeline orchestration."""

from unittest.mock import MagicMock

import pytest

from rag.pipeline import QueryResult, RAGPipeline


class TestRAGPipeline:
    def _make_pipeline(self, vectorstore=None, storage=None):
        vs = vectorstore or MagicMock()
        st = storage or MagicMock()
        return RAGPipeline(vectorstore=vs, storage=st, chunk_size=100, chunk_overlap=20)

    def test_ingest_page_stores_and_indexes(self):
        mock_vs = MagicMock()
        mock_storage = MagicMock()
        mock_storage.store_page.return_value = "W1/pages/about.html"

        pipeline = self._make_pipeline(vectorstore=mock_vs, storage=mock_storage)
        pipeline.ingest_page(
            workspace_id="W1",
            url="https://example.com/about",
            text="This is a long enough text for chunking. " * 5,
            raw_html="<html>content</html>",
        )

        mock_storage.store_page.assert_called_once()
        mock_storage.update_manifest.assert_called_once()
        mock_vs.upsert.assert_called_once()

    def test_ingest_page_chunks_text(self):
        mock_vs = MagicMock()
        mock_storage = MagicMock()
        mock_storage.store_page.return_value = "W1/pages/about.html"

        pipeline = self._make_pipeline(vectorstore=mock_vs, storage=mock_storage)
        long_text = "Word " * 200  # ~1000 chars, should produce multiple chunks
        pipeline.ingest_page(
            workspace_id="W1",
            url="https://example.com",
            text=long_text,
            raw_html="<html></html>",
        )

        upsert_kwargs = mock_vs.upsert.call_args[1]
        assert len(upsert_kwargs["texts"]) > 1
        assert len(upsert_kwargs["ids"]) > 1

    def test_query_returns_results_with_confidence(self):
        mock_vs = MagicMock()
        from rag.vectorstore import SearchResult

        mock_vs.search.return_value = [
            SearchResult(
                id="id1",
                score=0.9,
                text="Refund policy is 30 days",
                metadata={"source_url": "https://example.com"},
            ),
            SearchResult(
                id="id2",
                score=0.85,
                text="All refunds processed within 5 days",
                metadata={"source_url": "https://example.com"},
            ),
        ]

        pipeline = self._make_pipeline(vectorstore=mock_vs)
        result = pipeline.query(
            query="refund policy",
            workspace_id="W1",
            top_k=5,
        )

        assert isinstance(result, QueryResult)
        assert len(result.results) == 2
        assert result.confidence.score > 0
        assert result.query == "refund policy"

    def test_query_empty_results(self):
        mock_vs = MagicMock()
        mock_vs.search.return_value = []

        pipeline = self._make_pipeline(vectorstore=mock_vs)
        result = pipeline.query(query="nonexistent", workspace_id="W1", top_k=5)

        assert len(result.results) == 0
        assert result.confidence.score == 0.0

    def test_query_passes_metadata_filter(self):
        mock_vs = MagicMock()
        mock_vs.search.return_value = []

        pipeline = self._make_pipeline(vectorstore=mock_vs)
        pipeline.query(
            query="events",
            workspace_id="W1",
            top_k=5,
            filter_metadata={"category": "events"},
        )

        call_kwargs = mock_vs.search.call_args[1]
        assert call_kwargs["filter_metadata"] == {"category": "events"}

    def test_query_result_is_frozen(self):
        mock_vs = MagicMock()
        mock_vs.search.return_value = []

        pipeline = self._make_pipeline(vectorstore=mock_vs)
        result = pipeline.query(query="test", workspace_id="W1", top_k=5)

        with pytest.raises(AttributeError):
            result.query = "modified"


class TestExtractKeywords:
    """Tests for keyword extraction from queries."""

    def test_removes_stop_words(self):
        from rag.pipeline import _extract_keywords

        keywords = _extract_keywords("What is the refund policy?")
        assert "what" not in keywords
        assert "the" not in keywords
        assert "refund" in keywords
        assert "policy" in keywords

    def test_filters_short_words(self):
        from rag.pipeline import _extract_keywords

        keywords = _extract_keywords("I am a new volunteer")
        assert "am" not in keywords
        assert "volunteer" in keywords

    def test_empty_query_returns_empty(self):
        from rag.pipeline import _extract_keywords

        keywords = _extract_keywords("")
        assert keywords == set()

    def test_lowercases_keywords(self):
        from rag.pipeline import _extract_keywords

        keywords = _extract_keywords("Refund POLICY")
        assert "refund" in keywords
        assert "policy" in keywords
