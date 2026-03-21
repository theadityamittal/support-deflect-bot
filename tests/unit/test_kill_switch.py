"""Tests for kill switch Lambda."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from admin.kill_switch import lambda_handler


class TestKillSwitchLambda:
    @patch("admin.kill_switch._get_state_store")
    @patch("admin.kill_switch._disable_api_gateway")
    def test_activates_kill_switch(self, mock_disable_apigw, mock_get_store):
        mock_store = MagicMock()
        mock_get_store.return_value = mock_store

        sns_event = {
            "Records": [
                {
                    "Sns": {
                        "Message": json.dumps(
                            {
                                "budgetName": "sherpa-monthly",
                                "notificationType": "ACTUAL",
                                "threshold": "100",
                            }
                        )
                    }
                }
            ]
        }

        lambda_handler(sns_event, {})
        mock_store.set_kill_switch.assert_called_once_with(active=True)
        mock_disable_apigw.assert_called_once()

    @patch("admin.kill_switch._get_state_store")
    @patch("admin.kill_switch._disable_api_gateway")
    def test_handles_multiple_records(self, mock_disable_apigw, mock_get_store):
        mock_store = MagicMock()
        mock_get_store.return_value = mock_store

        sns_event = {
            "Records": [
                {"Sns": {"Message": "budget exceeded"}},
                {"Sns": {"Message": "budget exceeded again"}},
            ]
        }

        lambda_handler(sns_event, {})
        # Kill switch should only be set once regardless of record count
        mock_store.set_kill_switch.assert_called_once()
