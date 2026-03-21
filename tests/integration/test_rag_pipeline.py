"""Integration test: RAG pipeline scrape -> chunk -> store -> query."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from rag.chunker import chunk_text
from rag.confidence import calculate_confidence
from rag.pipeline import RAGPipeline
from rag.vectorstore import SearchResult


@pytest.mark.integration
class TestRAGPipelineIntegration:
    """Tests the full RAG pipeline with mocked external services."""

    def test_ingest_and_query_roundtrip(self):
        """Ingest a page, then query it and get results with confidence."""
        mock_vs = MagicMock()
        mock_storage = MagicMock()
        mock_storage.store_page.return_value = "W1/pages/about.html"

        # Simulate Pinecone returning what was ingested
        mock_vs.search.return_value = [
            SearchResult(
                id="W1_abc_0",
                score=0.92,
                text="Changing the Present helps communities through gift donations.",
                metadata={"source_url": "https://changingthepresent.org/about"},
            ),
        ]

        pipeline = RAGPipeline(
            vectorstore=mock_vs,
            storage=mock_storage,
            chunk_size=200,
            chunk_overlap=30,
        )

        # Ingest
        text = (
            "Changing the Present helps communities through gift donations. "
            "We channel to nonprofits some of the fortune people normally spend "
            "buying birthday, wedding, and holiday presents. Founded in 2005, "
            "we have served over 100,000 people through our platform."
        )
        num_chunks = pipeline.ingest_page(
            workspace_id="W1",
            url="https://changingthepresent.org/about",
            text=text,
            raw_html=f"<html><body>{text}</body></html>",
            metadata={"category": "general"},
        )
        assert num_chunks > 0

        # Verify storage was called correctly
        mock_storage.store_page.assert_called_once()
        mock_storage.update_manifest.assert_called_once()

        # Query
        result = pipeline.query(
            query="What does Changing the Present do?",
            workspace_id="W1",
            top_k=5,
        )

        assert len(result.results) == 1
        assert result.confidence.score > 0.3
        assert "Changing the Present" in result.results[0].text

    def test_ingest_multiple_pages_then_query(self):
        """Ingest multiple pages, query returns relevant results."""
        mock_vs = MagicMock()
        mock_storage = MagicMock()
        mock_storage.store_page.return_value = "W1/pages/page.html"

        pipeline = RAGPipeline(
            vectorstore=mock_vs,
            storage=mock_storage,
            chunk_size=200,
            chunk_overlap=30,
        )

        pages = [
            ("https://example.org/about", "We are a nonprofit helping communities."),
            (
                "https://example.org/events",
                "Our annual gala raises $50K for education.",
            ),
            (
                "https://example.org/volunteer",
                "Volunteers help 3 days per week.",
            ),
        ]

        for url, text in pages:
            pipeline.ingest_page(
                workspace_id="W1",
                url=url,
                text=text,
                raw_html=f"<html>{text}</html>",
            )

        assert mock_vs.upsert.call_count == 3
        assert mock_storage.store_page.call_count == 3
        assert mock_storage.update_manifest.call_count == 3

    def test_empty_page_produces_no_chunks(self):
        """Page with no meaningful text produces zero chunks."""
        mock_vs = MagicMock()
        mock_storage = MagicMock()
        mock_storage.store_page.return_value = "W1/pages/empty.html"

        pipeline = RAGPipeline(
            vectorstore=mock_vs,
            storage=mock_storage,
            chunk_size=200,
            chunk_overlap=30,
        )

        num_chunks = pipeline.ingest_page(
            workspace_id="W1",
            url="https://example.org/empty",
            text="   ",
            raw_html="<html></html>",
        )

        assert num_chunks == 0
        mock_vs.upsert.assert_not_called()

    def test_chunker_and_confidence_work_together(self):
        """Verify that chunks produced by chunker feed into confidence scoring."""
        text = (
            "Our nonprofit provides meals and shelter to underserved families. "
            "We serve over 500 families per week across 3 locations. "
            "Volunteers are the backbone of our organization."
        )

        chunks = chunk_text(text, chunk_size=100, chunk_overlap=20)
        assert len(chunks) > 0

        # Simulate search returning chunk texts with high scores
        similarity_scores = [0.88, 0.75]
        result_texts = [c.text for c in chunks[:2]]
        query_keywords = {"nonprofit", "meals", "shelter"}

        confidence = calculate_confidence(
            similarity_scores=similarity_scores,
            query_keywords=query_keywords,
            result_texts=result_texts,
            max_expected_results=5,
        )

        assert 0.0 < confidence.score <= 1.0
        assert "similarity" in confidence.breakdown
        assert "keyword_overlap" in confidence.breakdown

    def test_ingest_preserves_metadata_through_pipeline(self):
        """Metadata passed to ingest_page reaches vectorstore upsert."""
        mock_vs = MagicMock()
        mock_storage = MagicMock()
        mock_storage.store_page.return_value = "W1/pages/page.html"

        pipeline = RAGPipeline(
            vectorstore=mock_vs,
            storage=mock_storage,
            chunk_size=500,
            chunk_overlap=50,
        )

        pipeline.ingest_page(
            workspace_id="W1",
            url="https://example.org/events",
            text="Our annual gala raises funds for education programs.",
            raw_html="<html>gala</html>",
            metadata={"category": "events", "team": "fundraising"},
        )

        upsert_kwargs = mock_vs.upsert.call_args[1]
        # Each chunk's metadata should contain the source_url and custom fields
        for meta in upsert_kwargs["metadata_list"]:
            assert meta["source_url"] == "https://example.org/events"
            assert meta["category"] == "events"
            assert meta["team"] == "fundraising"

    def test_query_result_is_immutable(self):
        """QueryResult returned by pipeline is frozen (immutable)."""
        mock_vs = MagicMock()
        mock_vs.search.return_value = [
            SearchResult(
                id="id1",
                score=0.9,
                text="Some result text",
                metadata={},
            ),
        ]
        mock_storage = MagicMock()

        pipeline = RAGPipeline(
            vectorstore=mock_vs,
            storage=mock_storage,
            chunk_size=200,
            chunk_overlap=30,
        )

        result = pipeline.query(
            query="test query",
            workspace_id="W1",
            top_k=5,
        )

        with pytest.raises(AttributeError):
            result.query = "mutated"
