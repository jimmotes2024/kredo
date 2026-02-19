"""Trust analysis endpoints.

GET /trust/analysis/{pubkey} — full trust analysis for an agent
GET /trust/rings — network-wide ring report
GET /trust/network-health — aggregate network statistics
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from kredo.accountability import resolve_accountability_context
from kredo.api.deps import get_store
from kredo.store import KredoStore
from kredo.api.trust_cache import (
    get_cached_agent_analysis,
    get_cached_network_health,
    get_cached_rings,
)

router = APIRouter(prefix="/trust", tags=["trust-analysis"])


def _integrity_context(store: KredoStore, agent_pubkey: str) -> dict:
    baseline = store.get_active_integrity_baseline(agent_pubkey)
    latest_check = store.get_latest_integrity_check(agent_pubkey)

    if baseline is None:
        return {
            "traffic_light": "red",
            "status_label": "unknown_unsigned",
            "multiplier": 0.6,
            "recommended_action": "block_run",
            "active_baseline_id": None,
            "latest_check_id": latest_check["id"] if latest_check else None,
        }
    if latest_check is None:
        return {
            "traffic_light": "yellow",
            "status_label": "baseline_set_not_checked",
            "multiplier": 0.85,
            "recommended_action": "owner_review_required",
            "active_baseline_id": baseline["id"],
            "latest_check_id": None,
        }
    if latest_check.get("baseline_id") != baseline.get("id"):
        return {
            "traffic_light": "yellow",
            "status_label": "baseline_changed_recheck_required",
            "multiplier": 0.85,
            "recommended_action": "owner_review_required",
            "active_baseline_id": baseline["id"],
            "latest_check_id": latest_check["id"],
        }
    status = latest_check.get("status", "red")
    if status == "green":
        return {
            "traffic_light": "green",
            "status_label": "verified",
            "multiplier": 1.0,
            "recommended_action": "safe_to_run",
            "active_baseline_id": baseline["id"],
            "latest_check_id": latest_check["id"],
        }
    if status == "yellow":
        return {
            "traffic_light": "yellow",
            "status_label": "changed_since_baseline",
            "multiplier": 0.85,
            "recommended_action": "owner_review_required",
            "active_baseline_id": baseline["id"],
            "latest_check_id": latest_check["id"],
        }
    return {
        "traffic_light": "red",
        "status_label": "integrity_unknown",
        "multiplier": 0.6,
        "recommended_action": "block_run",
        "active_baseline_id": baseline["id"],
        "latest_check_id": latest_check["id"],
    }


@router.get("/analysis/{pubkey}")
async def trust_analysis(
    pubkey: str,
    store: KredoStore = Depends(get_store),
):
    """Full trust analysis: reputation, attestation weights, rings, weighted skills."""
    payload = get_cached_agent_analysis(store, pubkey)
    acct = resolve_accountability_context(store, pubkey)
    payload["accountability"] = {
        "tier": acct.tier,
        "multiplier": acct.multiplier,
        "owner_pubkey": acct.owner_pubkey,
        "ownership_claim_id": acct.ownership_claim_id,
    }
    integrity = _integrity_context(store, pubkey)
    payload["integrity"] = integrity
    combined_multiplier = acct.multiplier * integrity["multiplier"]
    payload["deployability_score"] = round(payload["reputation_score"] * combined_multiplier, 4)
    payload["deployability_multiplier"] = round(combined_multiplier, 4)
    return payload


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
