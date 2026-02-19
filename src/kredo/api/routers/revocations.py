"""Revocation and dispute endpoints.

POST /revoke — submit a signed revocation
POST /dispute — submit a signed dispute against a behavioral warning
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from kredo.api.deps import get_store
from kredo.api.rate_limit import submission_limiter
from kredo.api.trust_cache import invalidate_trust_cache
from kredo.exceptions import InvalidSignatureError
from kredo.models import Dispute, Revocation
from kredo.signing import verify_dispute, verify_revocation
from kredo.store import KredoStore

router = APIRouter(tags=["revocations"])


@router.post("/revoke")
async def submit_revocation(
    body: dict,
    store: KredoStore = Depends(get_store),
):
    """Submit a signed revocation to mark an attestation as revoked.

    Only the original attestor can revoke their own attestation.
    """
    try:
        rev = Revocation(**body)
    except Exception as e:
        return JSONResponse(
            status_code=422,
            content={"error": f"Invalid revocation: {e}"},
        )

    # Rate limit
    revoker_key = rev.revoker.pubkey
    if not submission_limiter.is_allowed(revoker_key, cooldown_seconds=60):
        remaining = submission_limiter.remaining_seconds(revoker_key, 60)
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limited",
                "retry_after_seconds": round(remaining, 1),
            },
        )

    # Verify signature
    if not rev.signature:
        return JSONResponse(
            status_code=400,
            content={"error": "Revocation must be signed"},
        )

    try:
        verify_revocation(rev)
    except InvalidSignatureError as e:
        return JSONResponse(
            status_code=400,
            content={"error": f"Signature verification failed: {e}"},
        )

    # Verify the attestation exists
    att = store.get_attestation(rev.attestation_id)
    if att is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Attestation not found: {rev.attestation_id}"},
        )

    # Verify revoker is the original attestor
    if att["attestor"]["pubkey"] != rev.revoker.pubkey:
        return JSONResponse(
            status_code=403,
            content={"error": "Only the original attestor can revoke an attestation"},
        )

    # Store revocation
    json_str = rev.model_dump_json()
    rev_id = store.save_revocation(json_str)
    invalidate_trust_cache()
    submission_limiter.record(revoker_key)

    return {
        "status": "revoked",
        "revocation_id": rev_id,
        "attestation_id": rev.attestation_id,
    }


@router.post("/dispute")
async def submit_dispute(
    body: dict,
    store: KredoStore = Depends(get_store),
):
    """Submit a signed dispute against a behavioral warning.

    Only the subject of the warning can dispute it.
    """
    try:
        disp = Dispute(**body)
    except Exception as e:
        return JSONResponse(
            status_code=422,
            content={"error": f"Invalid dispute: {e}"},
        )

    # Rate limit
    disputor_key = disp.disputor.pubkey
    if not submission_limiter.is_allowed(disputor_key, cooldown_seconds=60):
        remaining = submission_limiter.remaining_seconds(disputor_key, 60)
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limited",
                "retry_after_seconds": round(remaining, 1),
            },
        )

    # Verify signature
    if not disp.signature:
        return JSONResponse(
            status_code=400,
            content={"error": "Dispute must be signed"},
        )

    try:
        verify_dispute(disp)
    except InvalidSignatureError as e:
        return JSONResponse(
            status_code=400,
            content={"error": f"Signature verification failed: {e}"},
        )

    # Verify the warning exists and is a behavioral_warning
    warning = store.get_attestation(disp.warning_id)
    if warning is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Warning not found: {disp.warning_id}"},
        )
    if warning.get("type") != "behavioral_warning":
        return JSONResponse(
            status_code=422,
            content={"error": "Disputes can only be filed against behavioral warnings"},
        )

    # Verify disputor is the subject of the warning
    if warning["subject"]["pubkey"] != disp.disputor.pubkey:
        return JSONResponse(
            status_code=403,
            content={"error": "Only the subject of a warning can dispute it"},
        )

    # Store dispute
    json_str = disp.model_dump_json()
    disp_id = store.save_dispute(json_str)
    invalidate_trust_cache()
    submission_limiter.record(disputor_key)

    return {
        "status": "disputed",
        "dispute_id": disp_id,
        "warning_id": disp.warning_id,
    }
