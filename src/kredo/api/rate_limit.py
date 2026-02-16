"""In-memory rate limiting for the Kredo API.

Simple per-key throttle. Resets on server restart.
Not a security boundary — just spam prevention.
"""

from __future__ import annotations

import time


class RateLimiter:
    """In-memory rate limiter keyed by arbitrary string (pubkey, IP, etc.)."""

    def __init__(self):
        self._timestamps: dict[str, float] = {}

    def is_allowed(self, key: str, cooldown_seconds: int = 60) -> bool:
        """Check if a key is allowed to submit. Does NOT record the attempt."""
        last = self._timestamps.get(key)
        if last is None:
            return True
        return (time.monotonic() - last) >= cooldown_seconds

    def record(self, key: str) -> None:
        """Record a submission for a key."""
        self._timestamps[key] = time.monotonic()

    def remaining_seconds(self, key: str, cooldown_seconds: int = 60) -> float:
        """Seconds remaining before the key is allowed again."""
        last = self._timestamps.get(key)
        if last is None:
            return 0.0
        elapsed = time.monotonic() - last
        return max(0.0, cooldown_seconds - elapsed)


# Shared instances
submission_limiter = RateLimiter()
registration_limiter = RateLimiter()
