"""Tests for KMS field encryption."""

import base64
from unittest.mock import MagicMock, patch

import pytest

from security.crypto import FieldEncryptor


class TestFieldEncryptor:
    def _make_encryptor(self, mock_kms: MagicMock) -> FieldEncryptor:
        with patch("security.crypto.boto3.client", return_value=mock_kms):
            return FieldEncryptor(
                kms_key_id="arn:aws:kms:us-east-1:123456789012:key/test-key-id"
            )

    def test_encrypt_returns_base64_string(self):
        """encrypt() should return a base64-encoded string."""
        mock_kms = MagicMock()
        mock_kms.encrypt.return_value = {"CiphertextBlob": b"encrypted-bytes"}
        encryptor = self._make_encryptor(mock_kms)

        result = encryptor.encrypt("hello world")

        assert isinstance(result, str)
        # Verify it's valid base64
        decoded = base64.b64decode(result)
        assert decoded == b"encrypted-bytes"

    def test_decrypt_returns_original_plaintext(self):
        """decrypt() should return the original plaintext string."""
        mock_kms = MagicMock()
        mock_kms.decrypt.return_value = {"Plaintext": b"hello world"}
        encryptor = self._make_encryptor(mock_kms)

        ciphertext = base64.b64encode(b"some-encrypted-bytes").decode("utf-8")
        result = encryptor.decrypt(ciphertext)

        assert result == "hello world"

    def test_roundtrip_preserves_json_blob(self):
        """encrypt then decrypt should return the original JSON blob."""
        import json

        payload = json.dumps(
            {"token": "xoxb-secret", "team_id": "T123", "scopes": ["channels:read"]}
        )
        plaintext_bytes = payload.encode("utf-8")

        mock_kms = MagicMock()
        # encrypt call returns ciphertext
        mock_kms.encrypt.return_value = {"CiphertextBlob": b"opaque-ciphertext"}
        # decrypt call returns original plaintext bytes
        mock_kms.decrypt.return_value = {"Plaintext": plaintext_bytes}

        encryptor = self._make_encryptor(mock_kms)

        ciphertext = encryptor.encrypt(payload)
        decrypted = encryptor.decrypt(ciphertext)

        assert decrypted == payload
        recovered = json.loads(decrypted)
        assert recovered["token"] == "xoxb-secret"

    def test_decrypt_invalid_ciphertext_raises(self):
        """decrypt() with invalid base64 should raise ValueError."""
        mock_kms = MagicMock()
        encryptor = self._make_encryptor(mock_kms)

        with pytest.raises(ValueError, match="not valid base64"):
            encryptor.decrypt("not-valid-base64!!!")

    def test_encrypt_empty_string_raises(self):
        """encrypt() with an empty string should raise ValueError."""
        mock_kms = MagicMock()
        encryptor = self._make_encryptor(mock_kms)

        with pytest.raises(ValueError, match="plaintext"):
            encryptor.encrypt("")
