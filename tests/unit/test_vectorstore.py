"""Tests for Pinecone vectorstore client."""

from unittest.mock import MagicMock, patch

import pytest

from rag.vectorstore import PineconeVectorStore, SearchResult


class TestPineconeVectorStore:
    @patch("rag.vectorstore.Pinecone")
    def test_upsert_chunks(self, mock_pinecone_cls):
        mock_index = MagicMock()
        mock_pinecone_cls.return_value.Index.return_value = mock_index

        store = PineconeVectorStore(api_key="test-key", index_name="test-index")
        store.upsert(
            texts=["Hello world", "Goodbye"],
            ids=["id1", "id2"],
            namespace="workspace-1",
            metadata_list=[
                {"source_url": "https://example.com"},
                {"source_url": "https://example.com/bye"},
            ],
        )

        mock_index.upsert_records.assert_called_once()

    @patch("rag.vectorstore.Pinecone")
    def test_search_returns_results(self, mock_pinecone_cls):
        mock_index = MagicMock()
        mock_pinecone_cls.return_value.Index.return_value = mock_index
        mock_index.search.return_value = {
            "result": {
                "hits": [
                    {
                        "_id": "id1",
                        "_score": 0.92,
                        "fields": {
                            "chunk_text": "Refund policy...",
                            "source_url": "https://example.com",
                            "category": "policy",
                        },
                    },
                ]
            }
        }

        store = PineconeVectorStore(api_key="test-key", index_name="test-index")
        results = store.search(
            query="refund policy",
            namespace="workspace-1",
            top_k=5,
        )

        assert len(results) == 1
        assert isinstance(results[0], SearchResult)
        assert results[0].id == "id1"
        assert results[0].score == 0.92
        assert results[0].text == "Refund policy..."
        assert results[0].metadata["source_url"] == "https://example.com"

    @patch("rag.vectorstore.Pinecone")
    def test_search_with_metadata_filter(self, mock_pinecone_cls):
        mock_index = MagicMock()
        mock_pinecone_cls.return_value.Index.return_value = mock_index
        mock_index.search.return_value = {"result": {"hits": []}}

        store = PineconeVectorStore(api_key="test-key", index_name="test-index")
        store.search(
            query="events",
            namespace="workspace-1",
            top_k=3,
            filter_metadata={"category": "events"},
        )

        call_kwargs = mock_index.search.call_args[1]
        assert "filter" in call_kwargs

    @patch("rag.vectorstore.Pinecone")
    def test_search_empty_results(self, mock_pinecone_cls):
        mock_index = MagicMock()
        mock_pinecone_cls.return_value.Index.return_value = mock_index
        mock_index.search.return_value = {"result": {"hits": []}}

        store = PineconeVectorStore(api_key="test-key", index_name="test-index")
        results = store.search(query="nonexistent", namespace="ws-1", top_k=5)
        assert results == []

    @patch("rag.vectorstore.Pinecone")
    def test_delete_namespace(self, mock_pinecone_cls):
        mock_index = MagicMock()
        mock_pinecone_cls.return_value.Index.return_value = mock_index

        store = PineconeVectorStore(api_key="test-key", index_name="test-index")
        store.delete_namespace(namespace="workspace-1")
        mock_index.delete.assert_called_once_with(
            delete_all=True, namespace="workspace-1"
        )

    def test_search_result_is_frozen(self):
        result = SearchResult(id="id1", score=0.9, text="hello", metadata={})
        with pytest.raises(AttributeError):
            result.score = 0.5
