"""Deterministic JSON serialization for Ed25519 signing.

Produces a canonical byte representation by:
- Sorting keys recursively
- No whitespace (separators=(',', ':'))
- datetime → ISO 8601 string (UTC, with Z suffix)
- None values excluded
- Enum values serialized to their .value
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _normalize(obj: Any) -> Any:
    """Recursively normalize an object for canonical serialization."""
    if obj is None:
        return None
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        # Ensure UTC and use Z suffix
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=timezone.utc)
        return obj.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(obj, dict):
        return {
            k: _normalize(v)
            for k, v in sorted(obj.items())
            if v is not None
        }
    if isinstance(obj, (list, tuple)):
        return [_normalize(item) for item in obj]
    return obj


def canonical_json(obj: dict) -> bytes:
    """Produce canonical JSON bytes from a dict.

    The output is deterministic: same input always produces same bytes.
    Used as the signing payload for Ed25519 signatures.
    """
    normalized = _normalize(obj)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
