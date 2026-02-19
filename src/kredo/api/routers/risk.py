"""Risk and anti-gaming source-signal endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from kredo.api.deps import get_store
from kredo.store import KredoStore

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/source-anomalies")
async def source_anomalies(
    hours: int = Query(default=24, ge=1, le=24 * 30),
    min_events: int = Query(default=8, ge=1, le=1000),
    min_unique_actors: int = Query(default=4, ge=1, le=1000),
    limit: int = Query(default=100, ge=1, le=500),
    store: KredoStore = Depends(get_store),
):
    """Cluster write events by source and flag unusual concentration patterns.

    This is a risk signal only. It should not be used as sole enforcement proof.
    """
    anomalies = store.get_source_anomaly_signals(
        hours=hours,
        min_events=min_events,
        min_unique_actors=min_unique_actors,
        limit=limit,
    )
    return {
        "window_hours": hours,
        "thresholds": {
            "min_events": min_events,
            "min_unique_actors": min_unique_actors,
        },
        "cluster_count": len(anomalies),
        "clusters": anomalies,
    }

