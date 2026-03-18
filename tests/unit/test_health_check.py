"""Tests for Pinecone health check Lambda."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from admin.health_check import _get_pinecone_client, lambda_handler


class TestHealthCheckLambda:
    @patch("admin.health_check._get_pinecone_client")
    def test_healthy_index(self, mock_get_client):
        mock_client = MagicMock()
        mock_index = MagicMock()
        mock_index.describe_index_stats.return_value = MagicMock(total_vector_count=100)
        mock_client.Index.return_value = mock_index
        mock_get_client.return_value = mock_client

        result = lambda_handler({"source": "schedule"}, {})
        assert result["status"] == "healthy"
        assert result["vector_count"] == 100

    @patch("admin.health_check._get_pinecone_client")
    def test_unhealthy_index_logs_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.Index.side_effect = Exception("Connection refused")
        mock_get_client.return_value = mock_client

        result = lambda_handler({"source": "schedule"}, {})
        assert result["status"] == "unhealthy"
        assert "error" in result

    @patch("admin.health_check.boto3")
    @patch(
        "admin.health_check.os.environ",
        {
            "PINECONE_API_KEY_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123:secret:key"
        },
    )
    def test_get_pinecone_client_uses_secrets_manager(self, mock_boto3):
        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {"SecretString": "my-secret-key"}
        mock_boto3.client.return_value = mock_sm

        mock_pinecone_cls = MagicMock()
        with patch.dict(
            "sys.modules", {"pinecone": MagicMock(Pinecone=mock_pinecone_cls)}
        ):
            _get_pinecone_client()

        mock_boto3.client.assert_called_once_with("secretsmanager")
        mock_sm.get_secret_value.assert_called_once_with(
            SecretId="arn:aws:secretsmanager:us-east-1:123:secret:key"
        )

    @patch("admin.health_check.boto3")
    def test_get_pinecone_client_uses_env_var_fallback(self, mock_boto3):
        mock_pinecone_cls = MagicMock()
        with (
            patch.dict(os.environ, {"PINECONE_API_KEY": "env-key"}, clear=False),
            patch.dict(
                "sys.modules", {"pinecone": MagicMock(Pinecone=mock_pinecone_cls)}
            ),
        ):
            _get_pinecone_client()

        mock_boto3.client.assert_not_called()
        mock_pinecone_cls.assert_called_once_with(api_key="env-key")
