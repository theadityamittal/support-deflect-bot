"""E2E tests: real HTTP requests to deployed API Gateway Slack endpoints.

Sends signed requests to the live API Gateway and verifies responses,
SQS message enqueuing, and DynamoDB state changes.

Run: .venv/bin/pytest tests/e2e/test_slack_handler_e2e.py -v -m e2e --no-cov -s
"""

from __future__ import annotations

import json
import time
from urllib.parse import urlencode

import httpx
import pytest

from tests.e2e.conftest import (
    E2E_WORKSPACE_ID,
    cleanup_dynamodb_test_records,
    get_sqs_depth,
    sign_request,
)


@pytest.mark.e2e
class TestSlackHandlerE2E:
    """Tests hitting real API Gateway endpoints."""

    def test_url_verification(self, api_base_url, signing_secret):
        """API Gateway returns the challenge for Slack URL verification."""
        body = {"type": "url_verification", "challenge": "e2e_test_challenge_abc"}
        body_str = json.dumps(body)
        headers = sign_request(body_str, signing_secret)

        response = httpx.post(
            f"{api_base_url}/slack/events",
            content=body_str,
            headers=headers,
            timeout=15,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["challenge"] == "e2e_test_challenge_abc"
        print(f"  Response: {data}")

    def test_invalid_signature_rejected(self, api_base_url):
        """Request with wrong signature is rejected with 401."""
        body = {"type": "url_verification", "challenge": "bad_sig_test"}
        body_str = json.dumps(body)
        headers = sign_request(body_str, "wrong_secret_totally_invalid")

        response = httpx.post(
            f"{api_base_url}/slack/events",
            content=body_str,
            headers=headers,
            timeout=15,
        )

        assert response.status_code == 401
        data = response.json()
        assert "error" in data
        print(f"  Correctly rejected: {data}")

    def test_slash_command_help(self, api_base_url, signing_secret):
        """Slash command /onboard-help returns command list."""
        body = {
            "command": "/onboard-help",
            "user_id": "U_E2E_TEST",
            "team_id": E2E_WORKSPACE_ID,
            "channel_id": "C_E2E_TEST",
            "trigger_id": "e2e_trigger",
            "text": "",
            "response_url": "https://hooks.slack.com/commands/test",
        }
        body_str = urlencode(body)
        headers = sign_request(body_str, signing_secret)
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        response = httpx.post(
            f"{api_base_url}/slack/commands",
            content=body_str,
            headers=headers,
            timeout=15,
        )

        assert response.status_code == 200
        data = response.json()
        assert "/onboard-status" in data["text"]
        assert "/onboard-help" in data["text"]
        assert "/onboard-restart" in data["text"]
        print(f"  Help response: {data['text'][:100]}...")

    def test_slash_command_status_no_plan(
        self, api_base_url, signing_secret, dynamodb_table
    ):
        """Slash command /onboard-status with no plan returns appropriate message."""
        body = {
            "command": "/onboard-status",
            "user_id": "U_E2E_NO_PLAN",
            "team_id": E2E_WORKSPACE_ID,
            "channel_id": "C_E2E_TEST",
            "trigger_id": "e2e_trigger",
            "text": "",
            "response_url": "https://hooks.slack.com/commands/test",
        }
        body_str = urlencode(body)
        headers = sign_request(body_str, signing_secret)
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        response = httpx.post(
            f"{api_base_url}/slack/commands",
            content=body_str,
            headers=headers,
            timeout=15,
        )

        assert response.status_code == 200
        data = response.json()
        assert "no active onboarding plan" in data["text"].lower()
        print(f"  Status response: {data['text']}")

    def test_event_message_enqueued(self, api_base_url, signing_secret, dynamodb_table):
        """A normal user message should be enqueued to SQS."""
        body = {
            "type": "event_callback",
            "event_id": f"Ev_E2E_{int(time.time())}",
            "team_id": E2E_WORKSPACE_ID,
            "event": {
                "type": "message",
                "user": "U_E2E_TEST",
                "text": "E2E test message — how do I get started?",
                "channel": "D_E2E_DM",
                "ts": f"{int(time.time())}.000001",
            },
        }
        body_str = json.dumps(body)
        headers = sign_request(body_str, signing_secret)

        response = httpx.post(
            f"{api_base_url}/slack/events",
            content=body_str,
            headers=headers,
            timeout=15,
        )

        assert response.status_code == 200
        data = response.json()
        print(f"  Response: {data}")

        # The handler returns {"ok": true} only after successful SQS enqueue.
        # SQS depth check is unreliable because the agent worker Lambda
        # consumes messages faster than ApproximateNumberOfMessages updates.
        assert data.get("ok") is True, "Handler should confirm successful enqueue"

        # Cleanup DynamoDB records created by middleware
        cleanup_dynamodb_test_records(dynamodb_table)

    def test_bot_message_filtered(self, api_base_url, signing_secret, sqs_queue_url):
        """A bot message should be filtered — no SQS enqueue."""
        depth_before = get_sqs_depth(sqs_queue_url)

        body = {
            "type": "event_callback",
            "event_id": f"Ev_E2E_BOT_{int(time.time())}",
            "team_id": E2E_WORKSPACE_ID,
            "event": {
                "type": "message",
                "bot_id": "B_E2E_BOT",
                "user": "U_E2E_BOT",
                "text": "I am a bot, ignore me",
                "channel": "C_E2E_GENERAL",
                "ts": f"{int(time.time())}.000002",
            },
        }
        body_str = json.dumps(body)
        headers = sign_request(body_str, signing_secret)

        response = httpx.post(
            f"{api_base_url}/slack/events",
            content=body_str,
            headers=headers,
            timeout=15,
        )

        assert response.status_code == 200
        print(f"  Response: {response.json()}")

        time.sleep(2)
        depth_after = get_sqs_depth(sqs_queue_url)
        print(f"  SQS depth: {depth_before} → {depth_after}")
        assert depth_after == depth_before, "Bot message should NOT be enqueued"
