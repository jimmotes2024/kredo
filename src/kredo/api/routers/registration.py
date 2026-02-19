"""Agent registration endpoints.

POST /register — announce your existence (no signature required)
GET  /agents — list all registered agents/humans (paginated)
GET  /agents/{pubkey} — single agent details
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from kredo.api.deps import count_known_keys, get_known_key, get_store, list_known_keys
from kredo.api.rate_limit import registration_limiter
from kredo.store import KredoStore

router = APIRouter(tags=["registration"])

_PUBKEY_RE = re.compile(r"^ed25519:[0-9a-f]{64}$")


class RegisterRequest(BaseModel):
    pubkey: str
    name: str = ""
    type: str = "agent"

    @field_validator("pubkey")
    @classmethod
    def validate_pubkey(cls, v: str) -> str:
        if not _PUBKEY_RE.match(v):
            raise ValueError(
                "pubkey must be 'ed25519:' followed by 64 hex characters"
            )
        return v

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ("agent", "human"):
            raise ValueError("type must be 'agent' or 'human'")
        return v


@router.post("/register")
async def register_agent(
    body: RegisterRequest,
    request: Request,
    store: KredoStore = Depends(get_store),
):
    """Register a public key. No signature required — just announcing existence."""
    client_ip = request.client.host if request.client else "unknown"
    if not registration_limiter.is_allowed(client_ip, cooldown_seconds=60):
        remaining = registration_limiter.remaining_seconds(client_ip, 60)
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limited",
                "retry_after_seconds": round(remaining, 1),
            },
        )

    store.register_known_key(
        pubkey=body.pubkey,
        name=body.name,
        attestor_type=body.type,
    )
    registration_limiter.record(client_ip)

    return {
        "status": "registered",
        "pubkey": body.pubkey,
        "name": body.name,
        "type": body.type,
    }


@router.get("/agents")
async def list_agents(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    store: KredoStore = Depends(get_store),
):
    """List all registered agents/humans (paginated)."""
    agents = list_known_keys(store, limit=limit, offset=offset)
    total = count_known_keys(store)
    return {
        "agents": agents,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/agents/{pubkey}")
async def get_agent(
    pubkey: str,
    store: KredoStore = Depends(get_store),
):
    """Get a single registered agent by pubkey."""
    agent = get_known_key(store, pubkey)
    if agent is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Agent not found: {pubkey}"},
        )
    return agent
