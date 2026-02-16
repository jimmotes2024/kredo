"""Ed25519 signing and verification for attestations, disputes, and revocations."""

from __future__ import annotations

from nacl.encoding import HexEncoder
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from kredo._canonical import canonical_json
from kredo.exceptions import InvalidSignatureError
from kredo.models import Attestation, Dispute, Revocation


def _pubkey_to_verify_key(pubkey: str) -> VerifyKey:
    """Convert ed25519:<hex> pubkey string to a PyNaCl VerifyKey."""
    if not pubkey.startswith("ed25519:"):
        raise InvalidSignatureError(f"Invalid pubkey format: {pubkey}")
    hex_bytes = pubkey[len("ed25519:"):].encode("ascii")
    return VerifyKey(hex_bytes, encoder=HexEncoder)


def _signing_key_to_pubkey(signing_key: SigningKey) -> str:
    """Convert a PyNaCl SigningKey to ed25519:<hex> pubkey string."""
    return "ed25519:" + signing_key.verify_key.encode(encoder=HexEncoder).decode("ascii")


def _attestation_signable(attestation: Attestation) -> dict:
    """Build the signable dict from an attestation (everything except signature)."""
    data = attestation.model_dump(mode="json")
    data.pop("signature", None)
    return data


def _dispute_signable(dispute: Dispute) -> dict:
    """Build the signable dict from a dispute (everything except signature)."""
    data = dispute.model_dump(mode="json")
    data.pop("signature", None)
    return data


def _revocation_signable(revocation: Revocation) -> dict:
    """Build the signable dict from a revocation (everything except signature)."""
    data = revocation.model_dump(mode="json")
    data.pop("signature", None)
    return data


def sign_attestation(attestation: Attestation, signing_key: SigningKey) -> Attestation:
    """Sign an attestation with the given Ed25519 key.

    Returns a new Attestation with the signature field populated.
    Raises InvalidSignatureError if the signing key doesn't match the attestor pubkey.
    """
    expected_pubkey = _signing_key_to_pubkey(signing_key)
    if attestation.attestor.pubkey != expected_pubkey:
        raise InvalidSignatureError(
            "Signing key does not match attestor pubkey"
        )

    payload = canonical_json(_attestation_signable(attestation))
    signed = signing_key.sign(payload, encoder=HexEncoder)
    signature_hex = signed.signature.decode("ascii")

    return attestation.model_copy(update={"signature": f"ed25519:{signature_hex}"})


def verify_attestation(attestation: Attestation) -> bool:
    """Verify an attestation's Ed25519 signature.

    Returns True if valid. Raises InvalidSignatureError if invalid or missing.
    """
    if not attestation.signature:
        raise InvalidSignatureError("Attestation has no signature")

    if not attestation.signature.startswith("ed25519:"):
        raise InvalidSignatureError("Signature must start with 'ed25519:'")

    verify_key = _pubkey_to_verify_key(attestation.attestor.pubkey)
    payload = canonical_json(_attestation_signable(attestation))
    sig_hex = attestation.signature[len("ed25519:"):].encode("ascii")

    try:
        verify_key.verify(payload, HexEncoder.decode(sig_hex))
        return True
    except BadSignatureError:
        raise InvalidSignatureError("Attestation signature verification failed")


def sign_dispute(dispute: Dispute, signing_key: SigningKey) -> Dispute:
    """Sign a dispute with the given Ed25519 key."""
    expected_pubkey = _signing_key_to_pubkey(signing_key)
    if dispute.disputor.pubkey != expected_pubkey:
        raise InvalidSignatureError(
            "Signing key does not match disputor pubkey"
        )

    payload = canonical_json(_dispute_signable(dispute))
    signed = signing_key.sign(payload, encoder=HexEncoder)
    signature_hex = signed.signature.decode("ascii")

    return dispute.model_copy(update={"signature": f"ed25519:{signature_hex}"})


def verify_dispute(dispute: Dispute) -> bool:
    """Verify a dispute's Ed25519 signature."""
    if not dispute.signature:
        raise InvalidSignatureError("Dispute has no signature")
    if not dispute.signature.startswith("ed25519:"):
        raise InvalidSignatureError("Signature must start with 'ed25519:'")

    verify_key = _pubkey_to_verify_key(dispute.disputor.pubkey)
    payload = canonical_json(_dispute_signable(dispute))
    sig_hex = dispute.signature[len("ed25519:"):].encode("ascii")

    try:
        verify_key.verify(payload, HexEncoder.decode(sig_hex))
        return True
    except BadSignatureError:
        raise InvalidSignatureError("Dispute signature verification failed")


def sign_revocation(revocation: Revocation, signing_key: SigningKey) -> Revocation:
    """Sign a revocation with the given Ed25519 key."""
    expected_pubkey = _signing_key_to_pubkey(signing_key)
    if revocation.revoker.pubkey != expected_pubkey:
        raise InvalidSignatureError(
            "Signing key does not match revoker pubkey"
        )

    payload = canonical_json(_revocation_signable(revocation))
    signed = signing_key.sign(payload, encoder=HexEncoder)
    signature_hex = signed.signature.decode("ascii")

    return revocation.model_copy(update={"signature": f"ed25519:{signature_hex}"})


def verify_revocation(revocation: Revocation) -> bool:
    """Verify a revocation's Ed25519 signature."""
    if not revocation.signature:
        raise InvalidSignatureError("Revocation has no signature")
    if not revocation.signature.startswith("ed25519:"):
        raise InvalidSignatureError("Signature must start with 'ed25519:'")

    verify_key = _pubkey_to_verify_key(revocation.revoker.pubkey)
    payload = canonical_json(_revocation_signable(revocation))
    sig_hex = revocation.signature[len("ed25519:"):].encode("ascii")

    try:
        verify_key.verify(payload, HexEncoder.decode(sig_hex))
        return True
    except BadSignatureError:
        raise InvalidSignatureError("Revocation signature verification failed")
