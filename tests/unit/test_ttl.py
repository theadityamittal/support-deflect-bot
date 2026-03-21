"""Tests for TTL policy calculations."""

import time

from state.ttl import (
    ttl_for_daily_usage,
    ttl_for_google_oauth,
    ttl_for_injection_log,
    ttl_for_lock,
    ttl_for_monthly_usage,
    ttl_for_plan,
    ttl_for_setup,
)


class TestTTLPolicy:
    def test_plan_ttl_is_90_days(self):
        ttl = ttl_for_plan()
        now = int(time.time())
        days_90 = 90 * 24 * 60 * 60
        assert abs(ttl - (now + days_90)) < 5  # within 5s tolerance

    def test_lock_ttl_is_15_seconds(self):
        ttl = ttl_for_lock()
        now = int(time.time())
        assert abs(ttl - (now + 15)) < 5

    def test_lock_ttl_custom_seconds(self):
        ttl = ttl_for_lock(seconds=90)
        now = int(time.time())
        assert abs(ttl - (now + 90)) < 5

    def test_daily_usage_ttl_is_7_days(self):
        ttl = ttl_for_daily_usage()
        now = int(time.time())
        days_7 = 7 * 24 * 60 * 60
        assert abs(ttl - (now + days_7)) < 5

    def test_monthly_usage_ttl_is_30_days(self):
        ttl = ttl_for_monthly_usage()
        now = int(time.time())
        days_30 = 30 * 24 * 60 * 60
        assert abs(ttl - (now + days_30)) < 5

    def test_google_oauth_ttl_is_90_days(self):
        ttl = ttl_for_google_oauth()
        now = int(time.time())
        days_90 = 90 * 24 * 60 * 60
        assert abs(ttl - (now + days_90)) < 5

    def test_injection_log_ttl_is_90_days(self):
        ttl = ttl_for_injection_log()
        now = int(time.time())
        days_90 = 90 * 24 * 60 * 60
        assert abs(ttl - (now + days_90)) < 5

    def test_setup_ttl_is_14_days(self):
        ttl = ttl_for_setup()
        now = int(time.time())
        days_14 = 14 * 24 * 60 * 60
        assert abs(ttl - (now + days_14)) < 5

    def test_all_ttls_are_integers(self):
        assert isinstance(ttl_for_plan(), int)
        assert isinstance(ttl_for_lock(), int)
        assert isinstance(ttl_for_daily_usage(), int)
        assert isinstance(ttl_for_monthly_usage(), int)
        assert isinstance(ttl_for_google_oauth(), int)
        assert isinstance(ttl_for_injection_log(), int)
        assert isinstance(ttl_for_setup(), int)

    def test_all_ttls_are_in_the_future(self):
        now = int(time.time())
        assert ttl_for_plan() > now
        assert ttl_for_lock() > now
        assert ttl_for_daily_usage() > now
        assert ttl_for_monthly_usage() > now
        assert ttl_for_google_oauth() > now
        assert ttl_for_injection_log() > now
        assert ttl_for_setup() > now
