"""Tests for KredoSigningClient."""

import os
from unittest.mock import MagicMock, patch

import pytest
from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

from langchain_kredo._client import KredoSigningClient


class TestKeyResolution:
    """Test signing key resolution from various input formats."""

    def test_from_signing_key(self, keypair):
        sk, pubkey = keypair
        client = KredoSigningClient(signing_key=sk, api_url="http://test")
        assert client.pubkey == pubkey

    def test_from_bytes(self, keypair):
        sk, pubkey = keypair
        seed = sk.encode()  # 32-byte seed
        client = KredoSigningClient(signing_key=seed, api_url="http://test")
        assert client.pubkey == pubkey

    def test_from_hex_string(self, keypair):
        sk, pubkey = keypair
        seed_hex = sk.encode(encoder=HexEncoder).decode("ascii")
        client = KredoSigningClient(signing_key=seed_hex, api_url="http://test")
        assert client.pubkey == pubkey

    def test_from_env_var(self, keypair):
        sk, pubkey = keypair
        seed_hex = sk.encode(encoder=HexEncoder).decode("ascii")
        with patch.dict(os.environ, {"KREDO_PRIVATE_KEY": seed_hex}):
            client = KredoSigningClient(api_url="http://test")
            assert client.pubkey == pubkey

    def test_none_no_env(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("KREDO_PRIVATE_KEY", None)
            client = KredoSigningClient(api_url="http://test")
            assert client.pubkey is None

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError, match="Unsupported signing key type"):
            KredoSigningClient(signing_key=12345, api_url="http://test")


class TestPubkeyFormat:
    """Test that pubkey is in the correct ed25519:<hex> format."""

    def test_pubkey_format(self, keypair):
        sk, _ = keypair
        client = KredoSigningClient(signing_key=sk, api_url="http://test")
        assert client.pubkey.startswith("ed25519:")
        hex_part = client.pubkey[len("ed25519:"):]
        assert len(hex_part) == 64
        bytes.fromhex(hex_part)  # Should not raise


class TestWriteOperations:
    """Test that write operations build, sign, and submit."""

    def test_register_requires_key(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("KREDO_PRIVATE_KEY", None)
            client = KredoSigningClient(api_url="http://test")
            with pytest.raises(ValueError, match="Signing key required"):
                client.register()

    def test_attest_skill_builds_and_submits(self, mock_kredo_client, second_keypair):
        _, subject_pubkey = second_keypair
        mock_kredo_client._client.submit_attestation.return_value = {"id": "test-id"}

        result = mock_kredo_client.attest_skill(
            subject_pubkey=subject_pubkey,
            domain="security-operations",
            skill="vulnerability-assessment",
            proficiency=3,
            context="Demonstrated solid vulnerability assessment skills in CTF challenge",
        )

        assert result == {"id": "test-id"}
        mock_kredo_client._client.submit_attestation.assert_called_once()

        # Verify the submitted attestation is properly signed
        submitted = mock_kredo_client._client.submit_attestation.call_args[0][0]
        assert submitted["type"] == "skill_attestation"
        assert submitted["signature"] is not None
        assert submitted["signature"].startswith("ed25519:")
        assert submitted["skill"]["domain"] == "security-operations"
        assert submitted["skill"]["specific"] == "vulnerability-assessment"
        assert submitted["skill"]["proficiency"] == 3


class TestReadOperations:
    """Test that read operations delegate to the underlying client."""

    def test_health_delegates(self, mock_kredo_client):
        mock_kredo_client._client.health.return_value = {"status": "ok"}
        assert mock_kredo_client.health() == {"status": "ok"}

    def test_get_profile_delegates(self, mock_kredo_client):
        mock_kredo_client._client.get_profile.return_value = {"pubkey": "test"}
        assert mock_kredo_client.get_profile("test") == {"pubkey": "test"}

    def test_search_delegates(self, mock_kredo_client):
        mock_kredo_client._client.search.return_value = {"attestations": []}
        result = mock_kredo_client.search(domain="security")
        assert result == {"attestations": []}
        mock_kredo_client._client.search.assert_called_once_with(domain="security")

    def test_get_taxonomy_delegates(self, mock_kredo_client):
        mock_kredo_client._client.get_taxonomy.return_value = {"domains": []}
        assert mock_kredo_client.get_taxonomy() == {"domains": []}

    def test_my_profile_returns_own_profile(self, mock_kredo_client):
        mock_kredo_client._client.get_profile.return_value = {"pubkey": mock_kredo_client.pubkey}
        result = mock_kredo_client.my_profile()
        assert result["pubkey"] == mock_kredo_client.pubkey
        mock_kredo_client._client.get_profile.assert_called_with(mock_kredo_client.pubkey)

    def test_my_profile_requires_key(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("KREDO_PRIVATE_KEY", None)
            client = KredoSigningClient(api_url="http://test")
            with pytest.raises(ValueError, match="Signing key required"):
                client.my_profile()
