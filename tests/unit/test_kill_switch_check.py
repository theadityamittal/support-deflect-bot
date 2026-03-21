"""Tests for kill switch check with local cache."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import admin.kill_switch_check as _module


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset module-level cache between tests."""
    _module._cache["active"] = False
    _module._cache["checked_at"] = 0.0
    yield
    _module._cache["active"] = False
    _module._cache["checked_at"] = 0.0


class TestIsKillSwitchActive:
    def test_returns_false_when_inactive(self):
        mock_store = MagicMock()
        mock_store.get_kill_switch_status.return_value = False
        assert _module.is_kill_switch_active(mock_store) is False
        mock_store.get_kill_switch_status.assert_called_once()

    def test_returns_true_when_active(self):
        mock_store = MagicMock()
        mock_store.get_kill_switch_status.return_value = True
        assert _module.is_kill_switch_active(mock_store) is True

    def test_caches_result(self):
        mock_store = MagicMock()
        mock_store.get_kill_switch_status.return_value = False
        _module.is_kill_switch_active(mock_store)
        _module.is_kill_switch_active(mock_store)
        mock_store.get_kill_switch_status.assert_called_once()

    def test_refreshes_after_ttl(self):
        mock_store = MagicMock()
        mock_store.get_kill_switch_status.return_value = False
        _module.is_kill_switch_active(mock_store, cache_ttl=0)
        _module.is_kill_switch_active(mock_store, cache_ttl=0)
        assert mock_store.get_kill_switch_status.call_count == 2

    def test_cache_picks_up_state_change(self):
        mock_store = MagicMock()
        mock_store.get_kill_switch_status.return_value = False
        assert _module.is_kill_switch_active(mock_store, cache_ttl=0) is False
        mock_store.get_kill_switch_status.return_value = True
        assert _module.is_kill_switch_active(mock_store, cache_ttl=0) is True
