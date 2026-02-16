"""Tests for kredo.identity — keypair generation, storage, encryption."""

import pytest

from kredo.identity import (
    export_public_key,
    generate_keypair,
    get_default_identity,
    list_identities,
    load_signing_key,
    set_default_identity,
)
from kredo.models import AttestorType


class TestKeypairGeneration:
    def test_generate_unencrypted(self, store):
        identity = generate_keypair("test_agent", AttestorType.AGENT, store)
        assert identity.pubkey.startswith("ed25519:")
        assert identity.name == "test_agent"
        assert identity.type == AttestorType.AGENT

    def test_generate_encrypted(self, store):
        identity = generate_keypair("secure_agent", AttestorType.AGENT, store, passphrase="mypass")
        assert identity.pubkey.startswith("ed25519:")
        # Verify the key is stored as encrypted
        row = store.get_identity(identity.pubkey)
        assert row["is_encrypted"] == 1

    def test_first_identity_is_default(self, store):
        identity = generate_keypair("first", AttestorType.AGENT, store)
        default = get_default_identity(store)
        assert default is not None
        assert default.pubkey == identity.pubkey

    def test_second_identity_not_default(self, store):
        first = generate_keypair("first", AttestorType.AGENT, store)
        second = generate_keypair("second", AttestorType.AGENT, store)
        default = get_default_identity(store)
        assert default.pubkey == first.pubkey


class TestLoadSigningKey:
    def test_load_unencrypted(self, store):
        identity = generate_keypair("agent", AttestorType.AGENT, store)
        sk = load_signing_key(identity.pubkey, store)
        # Verify the loaded key matches
        from kredo.identity import _signing_key_to_pubkey
        assert _signing_key_to_pubkey(sk) == identity.pubkey

    def test_load_encrypted(self, store):
        identity = generate_keypair("agent", AttestorType.AGENT, store, passphrase="secret")
        sk = load_signing_key(identity.pubkey, store, passphrase="secret")
        from kredo.identity import _signing_key_to_pubkey
        assert _signing_key_to_pubkey(sk) == identity.pubkey

    def test_load_encrypted_wrong_passphrase(self, store):
        identity = generate_keypair("agent", AttestorType.AGENT, store, passphrase="right")
        with pytest.raises(Exception):  # nacl.exceptions.CryptoError
            load_signing_key(identity.pubkey, store, passphrase="wrong")

    def test_load_encrypted_no_passphrase(self, store):
        identity = generate_keypair("agent", AttestorType.AGENT, store, passphrase="right")
        from kredo.exceptions import KeyNotFoundError
        with pytest.raises(KeyNotFoundError, match="passphrase required"):
            load_signing_key(identity.pubkey, store)


class TestIdentityManagement:
    def test_list_identities(self, store):
        generate_keypair("a", AttestorType.AGENT, store)
        generate_keypair("b", AttestorType.HUMAN, store)
        ids = list_identities(store)
        assert len(ids) == 2
        names = {i.name for i in ids}
        assert names == {"a", "b"}

    def test_set_default(self, store):
        first = generate_keypair("first", AttestorType.AGENT, store)
        second = generate_keypair("second", AttestorType.AGENT, store)
        assert get_default_identity(store).pubkey == first.pubkey
        set_default_identity(second.pubkey, store)
        assert get_default_identity(store).pubkey == second.pubkey

    def test_export_public_key(self, store):
        identity = generate_keypair("agent", AttestorType.AGENT, store)
        hex_key = export_public_key(identity.pubkey)
        assert len(hex_key) == 64
        # Verify it's valid hex
        bytes.fromhex(hex_key)
