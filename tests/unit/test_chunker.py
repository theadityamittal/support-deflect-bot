"""Tests for document chunking with overlap."""

import pytest

from rag.chunker import chunk_text


class TestChunkText:
    """Test text chunking with configurable size and overlap."""

    def test_short_text_single_chunk(self):
        """Text shorter than chunk_size produces one chunk."""
        chunks = chunk_text("Hello world", chunk_size=100, chunk_overlap=10)
        assert len(chunks) == 1
        assert chunks[0].text == "Hello world"
        assert chunks[0].index == 0

    def test_exact_chunk_size(self):
        """Text exactly chunk_size long produces one chunk."""
        text = "a" * 100
        chunks = chunk_text(text, chunk_size=100, chunk_overlap=10)
        assert len(chunks) == 1

    def test_multiple_chunks_with_overlap(self):
        """Long text is split into overlapping chunks."""
        text = "word " * 100  # 500 chars
        chunks = chunk_text(text, chunk_size=100, chunk_overlap=20)
        assert len(chunks) > 1
        # Each chunk except the last should be close to chunk_size
        for chunk in chunks[:-1]:
            assert len(chunk.text) <= 100

    def test_overlap_content(self):
        """Adjacent chunks share overlapping content."""
        # Use a text where we can verify overlap
        words = [f"word{i}" for i in range(50)]
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=60, chunk_overlap=15)
        assert len(chunks) >= 2
        # There should be some shared text between adjacent chunks
        words_first = set(chunks[0].text.split())
        words_second = set(chunks[1].text.split())
        overlap_words = words_first & words_second
        assert len(overlap_words) > 0

    def test_empty_text_returns_empty(self):
        """Empty text returns empty list."""
        chunks = chunk_text("", chunk_size=100, chunk_overlap=10)
        assert chunks == []

    def test_whitespace_only_returns_empty(self):
        """Whitespace-only text returns empty list."""
        chunks = chunk_text("   \n\t  ", chunk_size=100, chunk_overlap=10)
        assert chunks == []

    def test_chunk_indices_are_sequential(self):
        """Chunk indices start at 0 and increment."""
        text = "word " * 200
        chunks = chunk_text(text, chunk_size=50, chunk_overlap=10)
        for i, chunk in enumerate(chunks):
            assert chunk.index == i

    def test_chunk_metadata_includes_source(self):
        """Chunks carry source metadata when provided."""
        chunks = chunk_text(
            "Hello world",
            chunk_size=100,
            chunk_overlap=10,
            metadata={"source_url": "https://example.com", "category": "general"},
        )
        assert chunks[0].metadata["source_url"] == "https://example.com"
        assert chunks[0].metadata["category"] == "general"

    def test_chunk_is_frozen(self):
        """Chunk dataclass is immutable."""
        chunks = chunk_text("Hello", chunk_size=100, chunk_overlap=10)
        with pytest.raises(AttributeError):
            chunks[0].text = "modified"

    def test_overlap_larger_than_size_raises(self):
        """Overlap >= chunk_size raises ValueError."""
        with pytest.raises(ValueError, match="overlap"):
            chunk_text("Hello world", chunk_size=10, chunk_overlap=10)

    def test_splits_on_sentence_boundaries_when_possible(self):
        """Chunker prefers splitting at sentence boundaries."""
        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        chunks = chunk_text(text, chunk_size=40, chunk_overlap=5)
        # At least one chunk should end at a sentence boundary
        has_sentence_boundary = any(c.text.rstrip().endswith(".") for c in chunks[:-1])
        assert has_sentence_boundary
