"""Trust analysis endpoints.

GET /trust/analysis/{pubkey} — full trust analysis for an agent
GET /trust/rings — network-wide ring report
GET /trust/network-health — aggregate network statistics
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from kredo.api.deps import get_store
from kredo.store import KredoStore
from kredo.api.trust_cache import (
    get_cached_agent_analysis,
    get_cached_network_health,
    get_cached_rings,
)

router = APIRouter(prefix="/trust", tags=["trust-analysis"])


@router.get("/analysis/{pubkey}")
async def trust_analysis(
    pubkey: str,
    store: KredoStore = Depends(get_store),
):
    """Full trust analysis: reputation, attestation weights, rings, weighted skills."""
    return get_cached_agent_analysis(store, pubkey)


@router.get("/rings")
async def rings_report(
    store: KredoStore = Depends(get_store),
):
    """Network-wide ring detection report."""
    return get_cached_rings(store)


@router.get("/network-health")
async def network_health(
    store: KredoStore = Depends(get_store),
):
    """Aggregate network statistics."""
    return get_cached_network_health(store)
