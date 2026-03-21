# tests/unit/test_signature.py
"""Tests for Slack HMAC-SHA256 signature verification."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from slack.signature import InvalidSignatureError, verify_slack_signature


class TestVerifySlackSignature:
    SIGNING_SECRET = "test_signing_secret_abc123"

    def _make_signature(self, body: str, timestamp: str) -> str:
        sig_basestring = f"v0:{timestamp}:{body}"
        h = hmac.new(
            self.SIGNING_SECRET.encode(),
            sig_basestring.encode(),
            hashlib.sha256,
        )
        return f"v0={h.hexdigest()}"

    def test_valid_signature(self):
        body = '{"event":{"type":"message"}}'
        ts = str(int(time.time()))
        sig = self._make_signature(body, ts)
        # Should not raise
        verify_slack_signature(
            signing_secret=self.SIGNING_SECRET,
            body=body,
            timestamp=ts,
            signature=sig,
        )

    def test_invalid_signature_raises(self):
        body = '{"event":{"type":"message"}}'
        ts = str(int(time.time()))
        with pytest.raises(InvalidSignatureError):
            verify_slack_signature(
                signing_secret=self.SIGNING_SECRET,
                body=body,
                timestamp=ts,
                signature="v0=invalid_hex_digest",
            )

    def test_expired_timestamp_raises(self):
        body = '{"event":{"type":"message"}}'
        old_ts = str(int(time.time()) - 600)  # 10 min old
        sig = self._make_signature(body, old_ts)
        with pytest.raises(InvalidSignatureError, match="expired"):
            verify_slack_signature(
                signing_secret=self.SIGNING_SECRET,
                body=body,
                timestamp=old_ts,
                signature=sig,
            )

    def test_tampered_body_raises(self):
        body = '{"event":{"type":"message"}}'
        ts = str(int(time.time()))
        sig = self._make_signature(body, ts)
        with pytest.raises(InvalidSignatureError):
            verify_slack_signature(
                signing_secret=self.SIGNING_SECRET,
                body=body + " tampered",
                timestamp=ts,
                signature=sig,
            )
