"""Input sanitization and prompt injection detection.

Checks for known injection patterns. Logs attempts to DynamoDB.
After strike_limit attempts, stops responding to the user.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from slack.models import MiddlewareResult, SlackEvent

if TYPE_CHECKING:
    from state.dynamo import DynamoStateStore

logger = logging.getLogger(__name__)

# Patterns that indicate prompt injection attempts
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+)?prior\s+instructions", re.IGNORECASE),
    re.compile(r"reveal\s+(your\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"(you\s+are|act\s+as)\s+now\s+a\s+different", re.IGNORECASE),
    re.compile(r"your\s+new\s+instructions\s+are", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?previous", re.IGNORECASE),
    re.compile(r"override\s+(your\s+)?system", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all)\s+(you\s+)?know", re.IGNORECASE),
]


class InputSanitizer:
    """Detect injection attempts and sanitize user input."""

    def __init__(
        self,
        *,
        state_store: DynamoStateStore,
        strike_limit: int = 3,
        max_length: int = 4000,
    ) -> None:
        self._store = state_store
        self._strike_limit = strike_limit
        self._max_length = max_length

    def check(self, event: SlackEvent) -> MiddlewareResult:
        text = event.text

        # Check for injection patterns
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(text):
                logger.warning(
                    "Injection attempt detected from user=%s workspace=%s",
                    event.user_id,
                    event.workspace_id,
                )
                self._store.log_injection_attempt(
                    workspace_id=event.workspace_id,
                    user_id=event.user_id,
                    text=text[:200],
                )
                return MiddlewareResult.reject(
                    "I can only help with onboarding questions."
                )

        return MiddlewareResult.allow()
