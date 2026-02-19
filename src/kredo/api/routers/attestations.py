"""Attestation submission and verification endpoints.

POST /attestations — submit a signed attestation
GET  /attestations/{id} — retrieve a single attestation
POST /verify — verify any signed JSON (attestation, dispute, or revocation)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from kredo.api.trust_cache import invalidate_trust_cache
from kredo.api.deps import get_store
from kredo.api.rate_limit import submission_limiter
from kredo.evidence import score_evidence
from kredo.exceptions import InvalidSignatureError
from kredo.models import Attestation, Dispute, Revocation
from kredo.signing import verify_attestation, verify_dispute, verify_revocation
from kredo.store import KredoStore

router = APIRouter(tags=["attestations"])


@router.post("/attestations")
async def submit_attestation(
    body: dict,
    store: KredoStore = Depends(get_store),
):
    """Submit a signed attestation for storage.

    Verifies the Ed25519 signature before storing.
    Rate limited: 1 submission per pubkey per 60 seconds.
    """
    # Parse into Attestation model (validates schema + taxonomy)
    try:
        att = Attestation(**body)
    except Exception as e:
        return JSONResponse(
            status_code=422,
            content={"error": f"Invalid attestation: {e}"},
        )

    # Rate limit by attestor pubkey
    attestor_key = att.attestor.pubkey
    if not submission_limiter.is_allowed(attestor_key, cooldown_seconds=60):
        remaining = submission_limiter.remaining_seconds(attestor_key, 60)
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limited",
                "retry_after_seconds": round(remaining, 1),
            },
        )

    # Verify signature
    if not att.signature:
        return JSONResponse(
            status_code=400,
            content={"error": "Attestation must be signed (signature field required)"},
        )

    try:
        verify_attestation(att)
    except InvalidSignatureError as e:
        return JSONResponse(
            status_code=400,
            content={"error": f"Signature verification failed: {e}"},
        )

    # Reject expired attestations
    now = datetime.now(timezone.utc)
    if att.expires.tzinfo is None:
        expires = att.expires.replace(tzinfo=timezone.utc)
    else:
        expires = att.expires
    if expires <= now:
        return JSONResponse(
            status_code=422,
            content={"error": "Attestation has already expired"},
        )

    # Score evidence
    ev_score = score_evidence(att.evidence, att.type)

    # Store
    json_str = att.model_dump_json()
    att_id = store.save_attestation(json_str)

    # Auto-register pubkeys
    store.register_known_key(att.attestor.pubkey, att.attestor.name, att.attestor.type.value)
    store.register_known_key(att.subject.pubkey, att.subject.name)
    invalidate_trust_cache()

    submission_limiter.record(attestor_key)

    return {
        "status": "accepted",
        "id": att_id,
        "evidence_score": {
            "composite": ev_score.composite,
            "specificity": ev_score.specificity,
            "verifiability": ev_score.verifiability,
            "relevance": ev_score.relevance,
            "recency": ev_score.recency,
        },
    }


@router.get("/attestations/{att_id}")
async def get_attestation(
    att_id: str,
    store: KredoStore = Depends(get_store),
):
    """Retrieve a single attestation by ID."""
    data = store.get_attestation(att_id)
    if data is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Attestation not found: {att_id}"},
        )
    # Add revocation status
    row = store.get_attestation_row(att_id)
    data["_meta"] = {
        "is_revoked": bool(row["is_revoked"]) if row else False,
        "imported_at": row["imported_at"] if row else None,
    }
    return data


@router.post("/verify")
async def verify_document(body: dict):
    """Verify any signed Kredo document (attestation, dispute, or revocation).

    Auto-detects the document type from its fields.
    """
    doc_type = _detect_type(body)
    if doc_type is None:
        return JSONResponse(
            status_code=422,
            content={"error": "Cannot determine document type. Expected attestation, dispute, or revocation fields."},
        )

    try:
        if doc_type == "attestation":
            att = Attestation(**body)
            verify_attestation(att)
            ev_score = score_evidence(att.evidence, att.type)
            # Check expiry
            now = datetime.now(timezone.utc)
            expires = att.expires if att.expires.tzinfo else att.expires.replace(tzinfo=timezone.utc)
            return {
                "valid": True,
                "type": "attestation",
                "attestation_type": att.type.value,
                "subject": att.subject.pubkey,
                "attestor": att.attestor.pubkey,
                "expired": expires <= now,
                "evidence_score": ev_score.composite,
            }

        elif doc_type == "dispute":
            disp = Dispute(**body)
            verify_dispute(disp)
            return {
                "valid": True,
                "type": "dispute",
                "warning_id": disp.warning_id,
                "disputor": disp.disputor.pubkey,
            }

        elif doc_type == "revocation":
            rev = Revocation(**body)
            verify_revocation(rev)
            return {
                "valid": True,
                "type": "revocation",
                "attestation_id": rev.attestation_id,
                "revoker": rev.revoker.pubkey,
            }

    except InvalidSignatureError as e:
        return {"valid": False, "type": doc_type, "error": str(e)}
    except Exception as e:
        return JSONResponse(
            status_code=422,
            content={"error": f"Invalid {doc_type}: {e}"},
        )


def _detect_type(body: dict) -> str | None:
    """Detect document type from its fields."""
    if "warning_id" in body and "disputor" in body:
        return "dispute"
    if "attestation_id" in body and "revoker" in body:
        return "revocation"
    if "attestor" in body and "subject" in body:
        return "attestation"
    return None
