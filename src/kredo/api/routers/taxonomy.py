"""Taxonomy browsing and custom domain/skill management endpoints.

GET /taxonomy — full taxonomy with all domains and skills
GET /taxonomy/{domain} — skills for one domain
POST /taxonomy/domains — create a custom domain (signed)
POST /taxonomy/domains/{domain}/skills — add a custom skill (signed)
DELETE /taxonomy/domains/{domain} — delete a custom domain (creator only, signed)
DELETE /taxonomy/domains/{domain}/skills/{skill} — delete a custom skill (creator only, signed)
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kredo.api.deps import get_known_key, get_store
from kredo.api.signatures import verify_signed_payload
from kredo.api.trust_cache import invalidate_trust_cache
from kredo.exceptions import StoreError
from kredo.store import KredoStore
from kredo.taxonomy import (
    get_domain_label,
    get_domains,
    get_skills,
    invalidate_cache,
    taxonomy_version,
)

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])

_PUBKEY_RE = re.compile(r"^ed25519:[0-9a-f]{64}$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


# --- Read endpoints (unchanged) ---


@router.get("")
async def full_taxonomy():
    """Return the complete skill taxonomy (bundled + custom)."""
    domains = get_domains()
    result = {}
    for domain in domains:
        result[domain] = {
            "label": get_domain_label(domain),
            "skills": get_skills(domain),
        }
    return {
        "version": taxonomy_version(),
        "domains": result,
    }


@router.get("/{domain}")
async def domain_skills(domain: str):
    """Return skills for a specific domain."""
    domains = get_domains()
    if domain not in domains:
        return JSONResponse(
            status_code=404,
            content={"error": f"Unknown domain: {domain!r}", "valid_domains": domains},
        )
    return {
        "domain": domain,
        "label": get_domain_label(domain),
        "skills": get_skills(domain),
    }


# --- Write endpoints (custom taxonomy) ---


class CreateDomainRequest(BaseModel):
    id: str
    label: str
    pubkey: str
    signature: str


class CreateSkillRequest(BaseModel):
    id: str
    pubkey: str
    signature: str


class DeleteRequest(BaseModel):
    pubkey: str
    signature: str


@router.post("/domains")
async def create_domain(
    body: CreateDomainRequest,
    store: KredoStore = Depends(get_store),
):
    """Create a custom taxonomy domain. Requires Ed25519 signature from a registered agent."""
    # Validate slug format
    if not _SLUG_RE.match(body.id):
        return JSONResponse(
            status_code=422,
            content={"error": "Domain ID must be a hyphenated lowercase slug (e.g. 'vise-operations')"},
        )
    if not _PUBKEY_RE.match(body.pubkey):
        return JSONResponse(
            status_code=422,
            content={"error": "Invalid pubkey format"},
        )

    # Verify agent is registered
    agent = get_known_key(store, body.pubkey)
    if agent is None:
        return JSONResponse(
            status_code=403,
            content={"error": "Agent not registered. Register first with POST /register"},
        )

    # Verify signature
    payload = {"action": "create_domain", "id": body.id, "label": body.label, "pubkey": body.pubkey}
    try:
        verify_signed_payload(payload, body.pubkey, body.signature)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    # Create domain
    try:
        store.create_custom_domain(body.id, body.label, body.pubkey)
        invalidate_cache()
        invalidate_trust_cache()
    except StoreError as e:
        return JSONResponse(status_code=409, content={"error": str(e)})

    return {"status": "created", "domain": body.id, "label": body.label}


@router.post("/domains/{domain}/skills")
async def create_skill(
    domain: str,
    body: CreateSkillRequest,
    store: KredoStore = Depends(get_store),
):
    """Add a custom skill to a domain. Requires Ed25519 signature from a registered agent."""
    if not _SLUG_RE.match(body.id):
        return JSONResponse(
            status_code=422,
            content={"error": "Skill ID must be a hyphenated lowercase slug (e.g. 'chain-orchestration')"},
        )
    if not _PUBKEY_RE.match(body.pubkey):
        return JSONResponse(
            status_code=422,
            content={"error": "Invalid pubkey format"},
        )

    agent = get_known_key(store, body.pubkey)
    if agent is None:
        return JSONResponse(
            status_code=403,
            content={"error": "Agent not registered. Register first with POST /register"},
        )

    payload = {"action": "create_skill", "domain": domain, "id": body.id, "pubkey": body.pubkey}
    try:
        verify_signed_payload(payload, body.pubkey, body.signature)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    try:
        store.create_custom_skill(domain, body.id, body.pubkey)
        invalidate_cache()
        invalidate_trust_cache()
    except StoreError as e:
        return JSONResponse(status_code=409, content={"error": str(e)})

    return {"status": "created", "domain": domain, "skill": body.id}


@router.delete("/domains/{domain}")
async def delete_domain(
    domain: str,
    body: DeleteRequest,
    store: KredoStore = Depends(get_store),
):
    """Delete a custom domain (creator only). Cascades to skills."""
    payload = {"action": "delete_domain", "domain": domain, "pubkey": body.pubkey}
    try:
        verify_signed_payload(payload, body.pubkey, body.signature)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    try:
        store.delete_custom_domain(domain, body.pubkey)
        invalidate_cache()
        invalidate_trust_cache()
    except StoreError as e:
        return JSONResponse(status_code=403, content={"error": str(e)})

    return {"status": "deleted", "domain": domain}


@router.delete("/domains/{domain}/skills/{skill}")
async def delete_skill(
    domain: str,
    skill: str,
    body: DeleteRequest,
    store: KredoStore = Depends(get_store),
):
    """Delete a custom skill (creator only)."""
    payload = {"action": "delete_skill", "domain": domain, "skill": skill, "pubkey": body.pubkey}
    try:
        verify_signed_payload(payload, body.pubkey, body.signature)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    try:
        store.delete_custom_skill(domain, skill, body.pubkey)
        invalidate_cache()
        invalidate_trust_cache()
    except StoreError as e:
        return JSONResponse(status_code=403, content={"error": str(e)})

    return {"status": "deleted", "domain": domain, "skill": skill}
