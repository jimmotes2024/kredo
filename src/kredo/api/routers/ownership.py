"""Ownership/accountability endpoints.

POST /ownership/claim   — agent-signed ownership claim
POST /ownership/confirm — human-signed ownership confirmation
POST /ownership/revoke  — signed revocation by agent or owner
GET  /ownership/agent/{pubkey} — ownership history for one agent
"""

from __future__ import annotations

import json
import re
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from kredo.api.deps import get_known_key, get_store
from kredo.api.signatures import verify_signed_payload
from kredo.api.trust_cache import invalidate_trust_cache
from kredo.exceptions import KeyNotFoundError, StoreError
from kredo.store import KredoStore

router = APIRouter(prefix="/ownership", tags=["ownership"])

_PUBKEY_RE = re.compile(r"^ed25519:[0-9a-f]{64}$")
_SIG_RE = re.compile(r"^ed25519:[0-9a-f]{128}$")
_CLAIM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,100}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _request_source(request: Request) -> tuple[str | None, str | None]:
    source_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return source_ip, user_agent


class OwnershipClaimRequest(BaseModel):
    claim_id: Optional[str] = None
    agent_pubkey: str
    human_pubkey: str
    signature: str

    @field_validator("claim_id")
    @classmethod
    def validate_claim_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not _CLAIM_ID_RE.match(v):
            raise ValueError("claim_id must match [A-Za-z0-9_-]{8,100}")
        return v

    @field_validator("agent_pubkey", "human_pubkey")
    @classmethod
    def validate_pubkey(cls, v: str) -> str:
        if not _PUBKEY_RE.match(v):
            raise ValueError("pubkey must be 'ed25519:' followed by 64 hex characters")
        return v

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, v: str) -> str:
        if not _SIG_RE.match(v):
            raise ValueError("signature must be 'ed25519:' followed by 128 hex characters")
        return v


class OwnershipConfirmRequest(BaseModel):
    claim_id: str
    human_pubkey: str
    signature: str
    contact_email: Optional[str] = None

    @field_validator("claim_id")
    @classmethod
    def validate_claim_id(cls, v: str) -> str:
        if not _CLAIM_ID_RE.match(v):
            raise ValueError("claim_id must match [A-Za-z0-9_-]{8,100}")
        return v

    @field_validator("human_pubkey")
    @classmethod
    def validate_pubkey(cls, v: str) -> str:
        if not _PUBKEY_RE.match(v):
            raise ValueError("human_pubkey must be 'ed25519:' followed by 64 hex characters")
        return v

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, v: str) -> str:
        if not _SIG_RE.match(v):
            raise ValueError("signature must be 'ed25519:' followed by 128 hex characters")
        return v

    @field_validator("contact_email")
    @classmethod
    def validate_contact_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        email = v.strip()
        if len(email) > 320 or not _EMAIL_RE.match(email):
            raise ValueError("contact_email must be a valid email address")
        return email


class OwnershipRevokeRequest(BaseModel):
    claim_id: str
    revoker_pubkey: str
    reason: str
    signature: str

    @field_validator("claim_id")
    @classmethod
    def validate_claim_id(cls, v: str) -> str:
        if not _CLAIM_ID_RE.match(v):
            raise ValueError("claim_id must match [A-Za-z0-9_-]{8,100}")
        return v

    @field_validator("revoker_pubkey")
    @classmethod
    def validate_pubkey(cls, v: str) -> str:
        if not _PUBKEY_RE.match(v):
            raise ValueError("revoker_pubkey must be 'ed25519:' followed by 64 hex characters")
        return v

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        value = v.strip()
        if len(value) < 8:
            raise ValueError("reason must be at least 8 characters")
        if len(value) > 500:
            raise ValueError("reason must be 500 characters or fewer")
        return value

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, v: str) -> str:
        if not _SIG_RE.match(v):
            raise ValueError("signature must be 'ed25519:' followed by 128 hex characters")
        return v


@router.post("/claim")
async def create_ownership_claim(
    body: OwnershipClaimRequest,
    request: Request,
    store: KredoStore = Depends(get_store),
):
    claim_id = body.claim_id or f"own-{uuid4().hex[:24]}"
    source_ip, user_agent = _request_source(request)

    try:
        agent = get_known_key(store, body.agent_pubkey)
        human = get_known_key(store, body.human_pubkey)
        if agent is None or agent.get("type") != "agent":
            return JSONResponse(
                status_code=404,
                content={"error": f"Agent key not found or not type=agent: {body.agent_pubkey}"},
            )
        if human is None or human.get("type") != "human":
            return JSONResponse(
                status_code=404,
                content={"error": f"Human key not found or not type=human: {body.human_pubkey}"},
            )

        payload = {
            "action": "ownership_claim",
            "claim_id": claim_id,
            "agent_pubkey": body.agent_pubkey,
            "human_pubkey": body.human_pubkey,
        }
        verify_signed_payload(payload, body.agent_pubkey, body.signature)

        store.create_ownership_claim(
            claim_id=claim_id,
            agent_pubkey=body.agent_pubkey,
            human_pubkey=body.human_pubkey,
            agent_signature=body.signature,
            claim_payload_json=json.dumps(payload, sort_keys=True),
        )
        invalidate_trust_cache()
        store.append_audit_event(
            action="ownership.claim",
            outcome="accepted",
            actor_pubkey=body.agent_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"claim_id": claim_id, "human_pubkey": body.human_pubkey},
        )
    except ValueError as e:
        store.append_audit_event(
            action="ownership.claim",
            outcome="rejected",
            actor_pubkey=body.agent_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"error": str(e), "claim_id": claim_id},
        )
        return JSONResponse(status_code=400, content={"error": str(e)})
    except StoreError as e:
        store.append_audit_event(
            action="ownership.claim",
            outcome="rejected",
            actor_pubkey=body.agent_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"error": str(e), "claim_id": claim_id},
        )
        return JSONResponse(status_code=409, content={"error": str(e)})

    return {
        "status": "pending",
        "claim_id": claim_id,
        "agent_pubkey": body.agent_pubkey,
        "human_pubkey": body.human_pubkey,
    }


@router.post("/confirm")
async def confirm_ownership_claim(
    body: OwnershipConfirmRequest,
    request: Request,
    store: KredoStore = Depends(get_store),
):
    source_ip, user_agent = _request_source(request)
    claim = store.get_ownership_claim(body.claim_id)
    if claim is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Ownership claim not found: {body.claim_id}"},
        )
    if claim["human_pubkey"] != body.human_pubkey:
        return JSONResponse(
            status_code=403,
            content={"error": "Only the designated human key can confirm this ownership claim"},
        )
    if claim["status"] != "pending":
        return JSONResponse(
            status_code=409,
            content={"error": f"Ownership claim must be pending (current: {claim['status']})"},
        )

    payload = {
        "action": "ownership_confirm",
        "claim_id": body.claim_id,
        "agent_pubkey": claim["agent_pubkey"],
        "human_pubkey": body.human_pubkey,
    }
    try:
        verify_signed_payload(payload, body.human_pubkey, body.signature)
        store.confirm_ownership_claim(
            claim_id=body.claim_id,
            human_signature=body.signature,
            confirm_payload_json=json.dumps(payload, sort_keys=True),
        )
        if body.contact_email:
            # Private metadata only (never included in public responses).
            store.upsert_human_contact_email(
                pubkey=body.human_pubkey,
                email=body.contact_email,
                email_verified=False,
            )
        invalidate_trust_cache()
        store.append_audit_event(
            action="ownership.confirm",
            outcome="accepted",
            actor_pubkey=body.human_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"claim_id": body.claim_id, "agent_pubkey": claim["agent_pubkey"]},
        )
    except ValueError as e:
        store.append_audit_event(
            action="ownership.confirm",
            outcome="rejected",
            actor_pubkey=body.human_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"error": str(e), "claim_id": body.claim_id},
        )
        return JSONResponse(status_code=400, content={"error": str(e)})
    except (StoreError, KeyNotFoundError) as e:
        store.append_audit_event(
            action="ownership.confirm",
            outcome="rejected",
            actor_pubkey=body.human_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"error": str(e), "claim_id": body.claim_id},
        )
        return JSONResponse(status_code=409, content={"error": str(e)})

    return {
        "status": "active",
        "claim_id": body.claim_id,
        "agent_pubkey": claim["agent_pubkey"],
        "human_pubkey": body.human_pubkey,
        "contact_email_saved": bool(body.contact_email),
    }


@router.post("/revoke")
async def revoke_ownership_claim(
    body: OwnershipRevokeRequest,
    request: Request,
    store: KredoStore = Depends(get_store),
):
    source_ip, user_agent = _request_source(request)
    claim = store.get_ownership_claim(body.claim_id)
    if claim is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Ownership claim not found: {body.claim_id}"},
        )
    if body.revoker_pubkey not in {claim["agent_pubkey"], claim["human_pubkey"]}:
        return JSONResponse(
            status_code=403,
            content={"error": "Only the linked agent or human owner can revoke this claim"},
        )
    payload = {
        "action": "ownership_revoke",
        "claim_id": body.claim_id,
        "agent_pubkey": claim["agent_pubkey"],
        "human_pubkey": claim["human_pubkey"],
        "revoker_pubkey": body.revoker_pubkey,
        "reason": body.reason,
    }
    try:
        verify_signed_payload(payload, body.revoker_pubkey, body.signature)
        store.revoke_ownership_claim(
            claim_id=body.claim_id,
            revoked_by=body.revoker_pubkey,
            reason=body.reason,
        )
        invalidate_trust_cache()
        store.append_audit_event(
            action="ownership.revoke",
            outcome="accepted",
            actor_pubkey=body.revoker_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"claim_id": body.claim_id},
        )
    except ValueError as e:
        store.append_audit_event(
            action="ownership.revoke",
            outcome="rejected",
            actor_pubkey=body.revoker_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"error": str(e), "claim_id": body.claim_id},
        )
        return JSONResponse(status_code=400, content={"error": str(e)})
    except (StoreError, KeyNotFoundError) as e:
        store.append_audit_event(
            action="ownership.revoke",
            outcome="rejected",
            actor_pubkey=body.revoker_pubkey,
            source_ip=source_ip,
            user_agent=user_agent,
            details={"error": str(e), "claim_id": body.claim_id},
        )
        return JSONResponse(status_code=409, content={"error": str(e)})

    return {"status": "revoked", "claim_id": body.claim_id}


@router.get("/agent/{agent_pubkey}")
async def ownership_for_agent(
    agent_pubkey: str,
    include_history: bool = Query(default=True),
    store: KredoStore = Depends(get_store),
):
    if not _PUBKEY_RE.match(agent_pubkey):
        return JSONResponse(
            status_code=422,
            content={"error": "agent_pubkey must be 'ed25519:' followed by 64 hex characters"},
        )

    active = store.get_active_owner(agent_pubkey)
    claims = store.list_ownership_for_agent(agent_pubkey) if include_history else []
    return {
        "agent_pubkey": agent_pubkey,
        "active_owner": active,
        "claims": claims,
    }
