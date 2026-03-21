"""Tests for 4-factor confidence scoring."""

import pytest

from rag.confidence import calculate_confidence


class TestCalculateConfidence:
    """Test the 4-factor confidence scoring with 40/20/20/20 weights."""

    def test_perfect_scores(self):
        """All perfect inputs yield confidence near 1.0."""
        result = calculate_confidence(
            similarity_scores=[0.95, 0.93, 0.91],
            query_keywords={"refund", "policy"},
            result_texts=[
                "The refund policy states that all customers are entitled to "
                "a full refund within 30 days of purchase. Please review our "
                "comprehensive guidelines for detailed information about the "
                "refund process and eligibility requirements. " * 3,
                "Our policy on refunds is very clear and straightforward "
                "for all customers who request assistance with returns. "
                "We aim to process all refund requests within five business "
                "days of receiving the returned merchandise. " * 3,
            ],
            max_expected_results=3,
        )
        assert result.score >= 0.8
        assert result.score <= 1.0

    def test_zero_results(self):
        """No results yield confidence 0."""
        result = calculate_confidence(
            similarity_scores=[],
            query_keywords={"refund"},
            result_texts=[],
            max_expected_results=5,
        )
        assert result.score == 0.0

    def test_low_similarity_low_confidence(self):
        """Low similarity scores produce low confidence."""
        result = calculate_confidence(
            similarity_scores=[0.1, 0.05],
            query_keywords={"unicorn"},
            result_texts=["Some text about cats"],
            max_expected_results=5,
        )
        assert result.score < 0.3

    def test_weight_distribution(self):
        """Weights sum to 1.0 and match spec (40/20/20/20)."""
        result = calculate_confidence(
            similarity_scores=[0.8],
            query_keywords={"test"},
            result_texts=["test content"],
            max_expected_results=5,
        )
        weights = result.breakdown
        assert abs(weights["similarity_weight"] - 0.4) < 0.01
        assert abs(weights["count_weight"] - 0.2) < 0.01
        assert abs(weights["keyword_weight"] - 0.2) < 0.01
        assert abs(weights["length_weight"] - 0.2) < 0.01

    def test_keyword_overlap_factor(self):
        """Higher keyword overlap increases confidence."""
        low = calculate_confidence(
            similarity_scores=[0.7],
            query_keywords={"refund", "policy", "returns"},
            result_texts=["Nothing relevant here"],
            max_expected_results=5,
        )
        high = calculate_confidence(
            similarity_scores=[0.7],
            query_keywords={"refund", "policy", "returns"},
            result_texts=["The refund policy covers returns and exchanges"],
            max_expected_results=5,
        )
        assert high.score > low.score

    def test_result_is_frozen(self):
        """ConfidenceResult is immutable."""
        result = calculate_confidence(
            similarity_scores=[0.5],
            query_keywords={"test"},
            result_texts=["test"],
            max_expected_results=5,
        )
        with pytest.raises(AttributeError):
            result.score = 0.99

    def test_score_clamped_to_0_1(self):
        """Score is always between 0 and 1."""
        result = calculate_confidence(
            similarity_scores=[1.0, 1.0, 1.0, 1.0, 1.0],
            query_keywords={"a"},
            result_texts=["a " * 1000] * 5,
            max_expected_results=5,
        )
        assert 0.0 <= result.score <= 1.0

    def test_content_length_factor(self):
        """Longer content increases confidence."""
        short = calculate_confidence(
            similarity_scores=[0.7],
            query_keywords=set(),
            result_texts=["Hi"],
            max_expected_results=5,
        )
        long = calculate_confidence(
            similarity_scores=[0.7],
            query_keywords=set(),
            result_texts=["This is a much longer piece of content " * 10],
            max_expected_results=5,
        )
        assert long.score > short.score
