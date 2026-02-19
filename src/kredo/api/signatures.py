"""Shared Ed25519 signature helpers for API routes."""

from __future__ import annotations

from nacl.encoding import HexEncoder
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from kredo._canonical import canonical_json


def verify_signed_payload(payload: dict, pubkey: str, signature: str) -> None:
    """Verify an Ed25519 signature over a canonical JSON payload."""
    if not pubkey.startswith("ed25519:"):
        raise ValueError("pubkey must start with 'ed25519:'")
    if not signature.startswith("ed25519:"):
        raise ValueError("signature must start with 'ed25519:'")

    pubkey_hex = pubkey[len("ed25519:"):].encode("ascii")
    sig_hex = signature[len("ed25519:"):].encode("ascii")

    verify_key = VerifyKey(pubkey_hex, encoder=HexEncoder)
    message = canonical_json(payload)

    try:
        verify_key.verify(message, HexEncoder.decode(sig_hex))
    except BadSignatureError:
        raise ValueError("Signature verification failed")
