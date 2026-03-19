"""E2E tests: real scrape → chunk → Pinecone upsert → search → confidence.

Hits real Pinecone index and real S3 bucket. No mocking.

Run: .venv/bin/pytest tests/e2e/test_rag_pipeline_e2e.py -v -m e2e --no-cov -s
"""

from __future__ import annotations

import time

import pytest
from rag.confidence import calculate_confidence
from rag.pipeline import RAGPipeline

# Sample content for ingestion (avoids depending on external HTTP for scraping)
SAMPLE_TEXT = (
    "Onboard Assist is a serverless Slack onboarding bot for nonprofits. "
    "It uses AWS Lambda, DynamoDB, and Pinecone for vector search. "
    "The bot generates personalized onboarding plans using Amazon Bedrock. "
    "New volunteers answer intake questions via Block Kit forms. "
    "The Plan+ReAct agent creates 5-8 step onboarding plans. "
    "Each step is executed via structured tool calls to Slack and Google Calendar. "
    "The system runs at $1-3 per month using a dual-model LLM router. "
    "Nova Micro handles reasoning tasks while Claude Haiku handles generation. "
    "A 6-layer middleware chain protects against prompt injection and abuse. "
    "Rate limiting uses DynamoDB conditional writes with 60-second TTL locks."
)

SAMPLE_HTML = f"<html><body><p>{SAMPLE_TEXT}</p></body></html>"
SAMPLE_URL = "https://example.com/onboard-assist/docs"


def _query_with_retry(pipeline, *, query, workspace_id, retries=3, delay=5):
    """Query Pinecone with retries for eventual consistency."""
    for attempt in range(retries):
        result = pipeline.query(query=query, workspace_id=workspace_id, top_k=5)
        if result.results:
            return result
        if attempt < retries - 1:
            time.sleep(delay)
    return result


@pytest.mark.e2e
class TestRAGPipelineE2E:
    """Tests hitting real Pinecone and real S3."""

    def test_scrape_chunk_upsert_query(
        self, pinecone_store, test_namespace, s3_storage
    ):
        """Full RAG roundtrip: ingest text → Pinecone → query → results."""
        pipeline = RAGPipeline(
            vectorstore=pinecone_store,
            storage=s3_storage,
            chunk_size=256,
            chunk_overlap=30,
        )

        # Ingest
        chunk_count = pipeline.ingest_page(
            workspace_id=test_namespace,
            url=SAMPLE_URL,
            text=SAMPLE_TEXT,
            raw_html=SAMPLE_HTML,
        )
        assert chunk_count > 0, "Should produce at least 1 chunk"
        print(f"  Ingested {chunk_count} chunks to namespace '{test_namespace}'")

        # Wait for Pinecone indexing
        time.sleep(10)

        # Query
        result = _query_with_retry(
            pipeline,
            query="How does the onboarding bot work?",
            workspace_id=test_namespace,
        )

        assert len(result.results) > 0, "Should return search results"
        assert result.confidence.score > 0, "Confidence should be positive"

        print(f"  Results: {len(result.results)}")
        print(f"  Confidence: {result.confidence.score}")
        print(f"  Top result score: {result.results[0].score}")
        print(f"  Top result text: {result.results[0].text[:100]}...")

    def test_s3_html_archival(self, s3_storage, s3_test_namespace):
        """Verify HTML is stored in real S3 bucket with manifest."""
        s3_key = s3_storage.store_page(
            workspace_id=s3_test_namespace,
            url=SAMPLE_URL,
            raw_html=SAMPLE_HTML,
        )
        assert s3_key.startswith(s3_test_namespace)
        print(f"  Stored at: s3://{s3_storage._bucket}/{s3_key}")

        s3_storage.update_manifest(
            workspace_id=s3_test_namespace,
            url=SAMPLE_URL,
            s3_key=s3_key,
            content_hash="abc123",
        )

        manifest = s3_storage.get_manifest(workspace_id=s3_test_namespace)
        assert len(manifest["pages"]) == 1
        assert manifest["pages"][0]["url"] == SAMPLE_URL
        print(f"  Manifest: {manifest}")

    def test_namespace_isolation(self, pinecone_store, test_namespace):
        """Querying a namespace with no data returns zero results."""
        results = pinecone_store.search(
            query="anything at all",
            namespace=f"{test_namespace}-isolated",
            top_k=5,
        )
        assert len(results) == 0, "Empty namespace should return no results"
        print("  Confirmed: empty namespace returns 0 results")

    def test_query_nonsense_low_confidence(self, pinecone_store, test_namespace):
        """Querying gibberish in an empty namespace yields zero confidence."""
        results = pinecone_store.search(
            query="xyzzy frobnicator quux",
            namespace=test_namespace,
            top_k=5,
        )

        confidence = calculate_confidence(
            similarity_scores=[r.score for r in results],
            query_keywords={"xyzzy", "frobnicator", "quux"},
            result_texts=[r.text for r in results],
            max_expected_results=5,
        )

        assert (
            confidence.score < 0.2
        ), f"Nonsense query should have low confidence, got {confidence.score}"
        print(f"  Confidence for nonsense query: {confidence.score}")
