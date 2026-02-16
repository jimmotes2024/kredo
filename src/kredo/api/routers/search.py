"""Search and trust graph endpoints.

GET /search — query attestations by criteria
GET /trust/who-attested/{pubkey} — all attestors for a subject
GET /trust/attested-by/{pubkey} — all subjects attested by an attestor
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from kredo.api.deps import get_store
from kredo.store import KredoStore

router = APIRouter(tags=["search"])


@router.get("/search")
async def search_attestations(
    subject: Optional[str] = Query(default=None, description="Subject pubkey"),
    attestor: Optional[str] = Query(default=None, description="Attestor pubkey"),
    domain: Optional[str] = Query(default=None, description="Skill domain"),
    skill: Optional[str] = Query(default=None, description="Specific skill"),
    type: Optional[str] = Query(default=None, alias="type", description="Attestation type"),
    min_proficiency: Optional[int] = Query(default=None, ge=1, le=5, description="Minimum proficiency level"),
    include_revoked: bool = Query(default=False, description="Include revoked attestations"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    store: KredoStore = Depends(get_store),
):
    """Search attestations by criteria. Paginated, sorted by issued date descending."""
    results = store.search_attestations(
        subject_pubkey=subject,
        attestor_pubkey=attestor,
        domain=domain,
        att_type=type,
        include_revoked=include_revoked,
    )

    # Post-query filtering for fields not in store API
    if skill:
        results = [
            r for r in results
            if r.get("skill", {}) and r["skill"].get("specific") == skill
        ]
    if min_proficiency is not None:
        results = [
            r for r in results
            if r.get("skill", {}) and r["skill"].get("proficiency", 0) >= min_proficiency
        ]

    total = len(results)
    page = results[offset : offset + limit]

    return {
        "attestations": page,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/trust/who-attested/{pubkey}")
async def who_attested(
    pubkey: str,
    store: KredoStore = Depends(get_store),
):
    """Get all attestors who have attested for a subject."""
    attestors = store.get_attestors_for(pubkey)
    return {
        "subject": pubkey,
        "attestors": attestors,
        "count": len(attestors),
    }


@router.get("/trust/attested-by/{pubkey}")
async def attested_by(
    pubkey: str,
    store: KredoStore = Depends(get_store),
):
    """Get all subjects attested by an attestor."""
    subjects = store.get_attested_by(pubkey)
    return {
        "attestor": pubkey,
        "subjects": subjects,
        "count": len(subjects),
    }
