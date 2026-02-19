"""Shared dependencies for the Kredo API."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from kredo.store import KredoStore

# Module-level store, initialized in app lifespan
_store: Optional[KredoStore] = None


def get_store() -> KredoStore:
    """FastAPI dependency: return the shared KredoStore instance."""
    if _store is None:
        raise RuntimeError("Store not initialized — app lifespan not started")
    return _store


def init_store(db_path: Optional[Path] = None) -> KredoStore:
    """Initialize the shared store. Called from app lifespan.

    Respects KREDO_DB_PATH environment variable if no explicit path given.
    """
    global _store
    if db_path is None:
        env_path = os.environ.get("KREDO_DB_PATH")
        if env_path:
            db_path = Path(env_path)
    _store = KredoStore(db_path=db_path)
    return _store


def close_store() -> None:
    """Close the shared store. Called from app lifespan."""
    global _store
    if _store is not None:
        _store.close()
        _store = None


# --- Helper queries not in Phase 1 store API ---


def list_known_keys(
    store: KredoStore,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List registered agents/humans from known_keys table."""
    return store.list_known_keys(limit=limit, offset=offset)


def get_known_key(store: KredoStore, pubkey: str) -> Optional[dict]:
    """Get a single known key by pubkey."""
    return store.get_known_key(pubkey)


def count_known_keys(store: KredoStore) -> int:
    """Count total registered keys."""
    return store.count_known_keys()


def count_attestations(store: KredoStore, include_revoked: bool = False) -> int:
    """Count total attestations."""
    if include_revoked:
        row = store._conn.execute("SELECT COUNT(*) as cnt FROM attestations").fetchone()
    else:
        row = store._conn.execute(
            "SELECT COUNT(*) as cnt FROM attestations WHERE is_revoked = 0"
        ).fetchone()
    return row["cnt"]
