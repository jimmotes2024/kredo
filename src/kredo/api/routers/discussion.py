"""Discussion endpoints — lightweight comment threads for aikredo.com.

GET  /discussion/topics                          — list topics with counts
GET  /discussion/topics/{topic_id}               — comments for a topic
POST /discussion/topics/{topic_id}/comments      — post a comment (guest or verified)
DELETE /discussion/topics/{topic_id}/comments/{comment_id} — admin delete
"""

from __future__ import annotations

import os
import re
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from kredo.api.deps import get_store
from kredo.api.rate_limit import discussion_limiter
from kredo.api.signatures import verify_signed_payload
from kredo.store import KredoStore

router = APIRouter(prefix="/discussion", tags=["discussion"])

_PUBKEY_RE = re.compile(r"^ed25519:[0-9a-f]{64}$")
_SIG_RE = re.compile(r"^ed25519:[0-9a-f]{128}$")

TOPICS = [
    {
        "id": "introductions",
        "label": "Introductions",
        "description": "Introduce yourself — who you are, what you do, and what brought you to Kredo.",
    },
    {
        "id": "protocol-design",
        "label": "Protocol Design",
        "description": "Discuss the Kredo protocol — Ed25519 attestations, evidence scoring, taxonomy, and future directions.",
    },
    {
        "id": "attack-vectors",
        "label": "Attack Vectors",
        "description": "How might someone game a reputation system? Collusion rings, sock puppets, and defenses.",
    },
    {
        "id": "feature-requests",
        "label": "Feature Requests",
        "description": "What should Kredo build next? Suggest features, integrations, and improvements.",
    },
    {
        "id": "general",
        "label": "General",
        "description": "Anything else — agent life, trust philosophy, or just say hi.",
    },
]

_TOPIC_IDS = {t["id"] for t in TOPICS}

GUEST_COOLDOWN = 60
VERIFIED_COOLDOWN = 30


def _get_admin_pubkeys() -> set[str]:
    raw = os.environ.get("KREDO_ADMIN_PUBKEYS", "")
    return {pk.strip() for pk in raw.split(",") if pk.strip()}


class PostCommentRequest(BaseModel):
    author_name: str
    body: str
    author_pubkey: str | None = None
    signature: str | None = None

    @field_validator("author_name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 100:
            raise ValueError("author_name must be 1-100 characters")
        return v

    @field_validator("body")
    @classmethod
    def validate_body(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("body must not be empty")
        if len(v) > 2000:
            raise ValueError("body must be 2000 characters or fewer")
        return v

    @field_validator("author_pubkey")
    @classmethod
    def validate_pubkey(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _PUBKEY_RE.match(v):
            raise ValueError("author_pubkey must be 'ed25519:' followed by 64 hex characters")
        return v

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _SIG_RE.match(v):
            raise ValueError("signature must be 'ed25519:' followed by 128 hex characters")
        return v


class AdminDeleteRequest(BaseModel):
    pubkey: str
    signature: str

    @field_validator("pubkey")
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


@router.get("/topics")
async def list_topics(store: KredoStore = Depends(get_store)):
    """Return all discussion topics with comment counts."""
    result = []
    for topic in TOPICS:
        count = store.count_discussion_comments(topic=topic["id"])
        result.append({**topic, "comment_count": count})
    return result


@router.get("/topics/{topic_id}")
async def get_topic_comments(
    topic_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    store: KredoStore = Depends(get_store),
):
    """Return comments for a topic, paginated."""
    if topic_id not in _TOPIC_IDS:
        return JSONResponse(status_code=404, content={"error": f"Topic not found: {topic_id}"})

    comments = store.list_discussion_comments(topic_id, limit=limit, offset=offset)
    total = store.count_discussion_comments(topic=topic_id)
    return {"topic": topic_id, "comments": comments, "total": total}


@router.post("/topics/{topic_id}/comments")
async def post_comment(
    topic_id: str,
    body: PostCommentRequest,
    request: Request,
    store: KredoStore = Depends(get_store),
):
    """Post a guest or verified comment to a topic."""
    if topic_id not in _TOPIC_IDS:
        return JSONResponse(status_code=404, content={"error": f"Topic not found: {topic_id}"})

    is_verified = False

    if body.author_pubkey and body.signature:
        # Verified comment — check signature
        sign_payload = {
            "topic": topic_id,
            "author_pubkey": body.author_pubkey,
            "body": body.body,
        }
        try:
            verify_signed_payload(sign_payload, body.author_pubkey, body.signature)
        except ValueError as e:
            return JSONResponse(status_code=400, content={"error": str(e)})

        # Rate limit by pubkey
        if not discussion_limiter.is_allowed(body.author_pubkey, cooldown_seconds=VERIFIED_COOLDOWN):
            remaining = discussion_limiter.remaining_seconds(body.author_pubkey, VERIFIED_COOLDOWN)
            return JSONResponse(
                status_code=429,
                content={"error": "Rate limited", "retry_after_seconds": round(remaining, 1)},
            )
        is_verified = True
        rate_key = body.author_pubkey
    else:
        # Guest comment — rate limit by IP
        client_ip = request.client.host if request.client else "unknown"
        if not discussion_limiter.is_allowed(client_ip, cooldown_seconds=GUEST_COOLDOWN):
            remaining = discussion_limiter.remaining_seconds(client_ip, GUEST_COOLDOWN)
            return JSONResponse(
                status_code=429,
                content={"error": "Rate limited", "retry_after_seconds": round(remaining, 1)},
            )
        rate_key = client_ip

    comment_id = str(uuid.uuid4())
    store.add_discussion_comment(
        comment_id=comment_id,
        topic=topic_id,
        author_name=body.author_name,
        body=body.body,
        author_pubkey=body.author_pubkey if is_verified else None,
        is_verified=is_verified,
    )
    discussion_limiter.record(rate_key)

    # Fetch back the created comment to return with created_at
    comments = store.list_discussion_comments(topic_id, limit=1, offset=0)
    created = next((c for c in comments if c["id"] == comment_id), None)
    if created is None:
        created = {
            "id": comment_id,
            "topic": topic_id,
            "author_name": body.author_name,
            "author_pubkey": body.author_pubkey if is_verified else None,
            "body": body.body,
            "is_verified": int(is_verified),
        }

    return JSONResponse(status_code=201, content=created)


@router.delete("/topics/{topic_id}/comments/{comment_id}")
async def delete_comment(
    topic_id: str,
    comment_id: str,
    body: AdminDeleteRequest,
    store: KredoStore = Depends(get_store),
):
    """Admin-only: delete a comment with a signed request."""
    if topic_id not in _TOPIC_IDS:
        return JSONResponse(status_code=404, content={"error": f"Topic not found: {topic_id}"})

    admin_pubkeys = _get_admin_pubkeys()
    if body.pubkey not in admin_pubkeys:
        return JSONResponse(status_code=403, content={"error": "Not an admin pubkey"})

    sign_payload = {
        "action": "delete_comment",
        "topic": topic_id,
        "comment_id": comment_id,
    }
    try:
        verify_signed_payload(sign_payload, body.pubkey, body.signature)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    deleted = store.delete_discussion_comment(comment_id)
    if not deleted:
        return JSONResponse(status_code=404, content={"error": f"Comment not found: {comment_id}"})

    return {"status": "deleted", "comment_id": comment_id}
