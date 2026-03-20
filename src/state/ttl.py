"""TTL policy for DynamoDB records.

Each record type has a specific TTL based on the spec:
- Active plans: 90 days from last activity
- Completion records: Never (no TTL)
- Daily usage: 7 days
- Monthly usage: 30 days
- Processing locks: 60 seconds
- Google OAuth tokens: 90 days
- Injection logs: 90 days
"""

from __future__ import annotations

import time

_SECONDS_PER_DAY = 86400


def ttl_for_plan() -> int:
    """90-day TTL for active onboarding plans."""
    return int(time.time()) + (90 * _SECONDS_PER_DAY)


def ttl_for_lock() -> int:
    """60-second TTL for processing locks."""
    return int(time.time()) + 60


def ttl_for_daily_usage() -> int:
    """7-day TTL for per-user daily usage records."""
    return int(time.time()) + (7 * _SECONDS_PER_DAY)


def ttl_for_monthly_usage() -> int:
    """30-day TTL for per-workspace monthly usage records."""
    return int(time.time()) + (30 * _SECONDS_PER_DAY)


def ttl_for_google_oauth() -> int:
    """90-day TTL for Google Calendar OAuth tokens."""
    return int(time.time()) + (90 * _SECONDS_PER_DAY)


def ttl_for_injection_log() -> int:
    """90-day TTL for injection attempt log records."""
    return int(time.time()) + (90 * _SECONDS_PER_DAY)


def ttl_for_secrets() -> int:
    """90-day TTL for workspace secrets records."""
    return int(time.time()) + (90 * _SECONDS_PER_DAY)


def ttl_for_setup() -> int:
    """7-day TTL for admin setup state records."""
    return int(time.time()) + (7 * _SECONDS_PER_DAY)
