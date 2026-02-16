"""Ed25519 identity management — keypair generation, encrypted storage, loading.

Key format: ed25519:<hex-encoded-32-byte-public-key>
Private keys encrypted at rest via argon2 (PyNaCl nacl.pwhash).
Passphrase-free mode for automated agents (warns on CLI).
"""

from __future__ import annotations

import logging
from typing import Optional

from nacl.encoding import HexEncoder, RawEncoder
from nacl.pwhash import argon2id
from nacl.signing import SigningKey
from nacl.utils import random as nacl_random

from kredo.exceptions import KeyNotFoundError
from kredo.models import AttestorType, Identity
from kredo.store import KredoStore

logger = logging.getLogger(__name__)

# Encryption constants
_SALT_SIZE = 16
_NONCE_SIZE = 24  # SecretBox nonce
_KEY_SIZE = 32


def _signing_key_to_pubkey(signing_key: SigningKey) -> str:
    """Convert a PyNaCl SigningKey to ed25519:<hex> pubkey string."""
    return "ed25519:" + signing_key.verify_key.encode(encoder=HexEncoder).decode("ascii")


def _encrypt_seed(seed: bytes, passphrase: str) -> bytes:
    """Encrypt a 32-byte seed using argon2id key derivation + secretbox.

    Returns: salt (16) + nonce (24) + ciphertext (48 = 32 seed + 16 mac)
    """
    from nacl.secret import SecretBox

    salt = nacl_random(_SALT_SIZE)
    key = argon2id.kdf(
        _KEY_SIZE,
        passphrase.encode("utf-8"),
        salt,
        opslimit=argon2id.OPSLIMIT_INTERACTIVE,
        memlimit=argon2id.MEMLIMIT_INTERACTIVE,
    )
    box = SecretBox(key)
    nonce = nacl_random(_NONCE_SIZE)
    encrypted = box.encrypt(seed, nonce=nonce, encoder=RawEncoder)
    # encrypted = nonce + ciphertext, but we control the nonce, so store separately
    # Actually box.encrypt prepends nonce. Let's use that directly.
    return salt + encrypted  # salt(16) + nonce(24) + ciphertext(48)


def _decrypt_seed(blob: bytes, passphrase: str) -> bytes:
    """Decrypt a seed blob. Returns the 32-byte seed."""
    from nacl.secret import SecretBox

    salt = blob[:_SALT_SIZE]
    encrypted = blob[_SALT_SIZE:]  # nonce + ciphertext (SecretBox format)
    key = argon2id.kdf(
        _KEY_SIZE,
        passphrase.encode("utf-8"),
        salt,
        opslimit=argon2id.OPSLIMIT_INTERACTIVE,
        memlimit=argon2id.MEMLIMIT_INTERACTIVE,
    )
    box = SecretBox(key)
    return box.decrypt(encrypted, encoder=RawEncoder)


def generate_keypair(
    name: str,
    attestor_type: AttestorType,
    store: KredoStore,
    passphrase: Optional[str] = None,
) -> Identity:
    """Generate a new Ed25519 keypair and save to the store.

    Args:
        name: Human-readable name for this identity.
        attestor_type: "agent" or "human".
        store: KredoStore instance for persistence.
        passphrase: If provided, encrypt the private key at rest.
                    If None, store unencrypted (warns in logs).

    Returns:
        Identity model with the public key.
    """
    signing_key = SigningKey.generate()
    pubkey = _signing_key_to_pubkey(signing_key)
    seed = signing_key.encode(encoder=RawEncoder)

    if passphrase:
        encrypted_seed = _encrypt_seed(seed, passphrase)
        is_encrypted = True
    else:
        logger.warning(
            "Generating keypair without passphrase — private key stored unencrypted. "
            "This is acceptable for automated agents but not recommended for humans."
        )
        encrypted_seed = seed
        is_encrypted = False

    # Check if this is the first identity — make it default
    existing = store.list_identities()
    is_default = len(existing) == 0

    store.save_identity(
        pubkey=pubkey,
        name=name,
        attestor_type=attestor_type.value,
        private_key_encrypted=encrypted_seed,
        is_encrypted=is_encrypted,
        is_default=is_default,
    )

    return Identity(
        pubkey=pubkey,
        name=name,
        type=attestor_type,
    )


def load_signing_key(
    pubkey: str,
    store: KredoStore,
    passphrase: Optional[str] = None,
) -> SigningKey:
    """Load a signing key from the store.

    Args:
        pubkey: The ed25519:<hex> public key.
        store: KredoStore instance.
        passphrase: Required if the key was stored encrypted.

    Returns:
        PyNaCl SigningKey ready for signing.

    Raises:
        KeyNotFoundError: If the key doesn't exist.
        nacl.exceptions.CryptoError: If passphrase is wrong.
    """
    key_blob, is_encrypted = store.get_private_key(pubkey)

    if is_encrypted:
        if not passphrase:
            raise KeyNotFoundError(
                f"Key {pubkey} is encrypted — passphrase required"
            )
        seed = _decrypt_seed(key_blob, passphrase)
    else:
        seed = key_blob

    return SigningKey(seed)


def list_identities(store: KredoStore) -> list[Identity]:
    """List all local identities."""
    rows = store.list_identities()
    return [
        Identity(
            pubkey=r["pubkey"],
            name=r["name"],
            type=AttestorType(r["type"]),
        )
        for r in rows
    ]


def get_default_identity(store: KredoStore) -> Optional[Identity]:
    """Get the default identity, or None if no identities exist."""
    row = store.get_default_identity()
    if row is None:
        return None
    return Identity(
        pubkey=row["pubkey"],
        name=row["name"],
        type=AttestorType(row["type"]),
    )


def set_default_identity(pubkey: str, store: KredoStore) -> None:
    """Set an identity as the default."""
    store.set_default_identity(pubkey)


def export_public_key(pubkey: str) -> str:
    """Export the hex-encoded public key portion for sharing."""
    if not pubkey.startswith("ed25519:"):
        raise ValueError("Invalid pubkey format")
    return pubkey[len("ed25519:"):]
