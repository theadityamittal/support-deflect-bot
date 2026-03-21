"""Kill switch check with per-cold-start local cache.

Checks DynamoDB at most once per cache_ttl seconds.
Used by both handler and worker Lambdas.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from state.dynamo import DynamoStateStore

_cache: dict[str, float | bool] = {"active": False, "checked_at": 0.0}


def is_kill_switch_active(state_store: DynamoStateStore, cache_ttl: int = 60) -> bool:
    """Return True if the global kill switch is active. Cached for cache_ttl seconds."""
    if time.time() - float(_cache["checked_at"]) < cache_ttl:
        return bool(_cache["active"])
    _cache["active"] = state_store.get_kill_switch_status()
    _cache["checked_at"] = time.time()
    return bool(_cache["active"])
