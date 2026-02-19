"""Small in-process TTL cache for trust-analysis endpoints.

This is intentionally simple and process-local. It reduces repeated ring/
reputation recomputation during active browsing and is invalidated on writes.
"""

from __future__ import annotations

import copy
import os
import threading
import time
from dataclasses import asdict

from kredo.store import KredoStore
from kredo.trust_analysis import analyze_agent, compute_network_health, detect_all_rings

_CACHE_LOCK = threading.RLock()
_CACHE: dict[str, tuple[float, object]] = {}

_TRUST_PREFIX = "trust:"
_DEFAULT_TTL_SECONDS = 30
_MAX_CACHE_ITEMS = 2048


def _now() -> float:
    return time.monotonic()


def _get_ttl_seconds() -> int:
    raw = os.environ.get("KREDO_TRUST_CACHE_TTL_SECONDS", str(_DEFAULT_TTL_SECONDS))
    try:
        ttl = int(raw)
    except ValueError:
        ttl = _DEFAULT_TTL_SECONDS
    return max(ttl, 0)


def _get_cached(key: str):
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= _now():
            _CACHE.pop(key, None)
            return None
        return copy.deepcopy(value)


def _set_cached(key: str, value: object, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    with _CACHE_LOCK:
        if len(_CACHE) >= _MAX_CACHE_ITEMS:
            # Cheap bounded behavior: evict oldest-expiring entry.
            oldest_key = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest_key, None)
        _CACHE[key] = (_now() + ttl_seconds, copy.deepcopy(value))


def invalidate_trust_cache() -> None:
    """Clear all cached trust-analysis entries."""
    with _CACHE_LOCK:
        for key in [k for k in _CACHE if k.startswith(_TRUST_PREFIX)]:
            _CACHE.pop(key, None)


def get_cached_agent_analysis(store: KredoStore, pubkey: str) -> dict:
    """Return cached trust analysis payload, computing if needed."""
    key = f"{_TRUST_PREFIX}analysis:{pubkey}"
    cached = _get_cached(key)
    if cached is not None:
        return cached

    analysis = analyze_agent(store, pubkey)
    payload = {
        "pubkey": analysis.pubkey,
        "reputation_score": analysis.reputation_score,
        "attestation_weights": [asdict(w) for w in analysis.attestation_weights],
        "rings_involved": [asdict(r) for r in analysis.rings_involved],
        "weighted_skills": analysis.weighted_skills,
        "analysis_timestamp": analysis.analysis_timestamp,
    }
    _set_cached(key, payload, _get_ttl_seconds())
    return payload


def get_cached_rings(store: KredoStore) -> dict:
    """Return cached ring-report payload, computing if needed."""
    key = f"{_TRUST_PREFIX}rings"
    cached = _get_cached(key)
    if cached is not None:
        return cached

    rings = detect_all_rings(store)
    payload = {
        "ring_count": len(rings),
        "rings": [asdict(r) for r in rings],
    }
    _set_cached(key, payload, _get_ttl_seconds())
    return payload


def get_cached_network_health(store: KredoStore) -> dict:
    """Return cached network-health payload, computing if needed."""
    key = f"{_TRUST_PREFIX}network-health"
    cached = _get_cached(key)
    if cached is not None:
        return cached

    payload = compute_network_health(store)
    _set_cached(key, payload, _get_ttl_seconds())
    return payload
