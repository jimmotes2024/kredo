"""Trust analysis endpoints.

GET /trust/analysis/{pubkey} — full trust analysis for an agent
GET /trust/rings — network-wide ring report
GET /trust/network-health — aggregate network statistics
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from kredo.api.deps import get_store
from kredo.store import KredoStore
from kredo.trust_analysis import (
    analyze_agent,
    compute_network_health,
    detect_all_rings,
)

router = APIRouter(prefix="/trust", tags=["trust-analysis"])


@router.get("/analysis/{pubkey}")
async def trust_analysis(
    pubkey: str,
    store: KredoStore = Depends(get_store),
):
    """Full trust analysis: reputation, attestation weights, rings, weighted skills."""
    analysis = analyze_agent(store, pubkey)
    return {
        "pubkey": analysis.pubkey,
        "reputation_score": analysis.reputation_score,
        "attestation_weights": [asdict(w) for w in analysis.attestation_weights],
        "rings_involved": [asdict(r) for r in analysis.rings_involved],
        "weighted_skills": analysis.weighted_skills,
        "analysis_timestamp": analysis.analysis_timestamp,
    }


@router.get("/rings")
async def rings_report(
    store: KredoStore = Depends(get_store),
):
    """Network-wide ring detection report."""
    rings = detect_all_rings(store)
    return {
        "ring_count": len(rings),
        "rings": [asdict(r) for r in rings],
    }


@router.get("/network-health")
async def network_health(
    store: KredoStore = Depends(get_store),
):
    """Aggregate network statistics."""
    return compute_network_health(store)
