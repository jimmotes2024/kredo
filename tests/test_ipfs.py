"""Tests for kredo.ipfs — IPFS pinning, fetching, canonical JSON, CLI commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from typer.testing import CliRunner

from kredo._canonical import _normalize
from kredo.cli import app
from kredo.exceptions import IPFSError
from kredo.identity import generate_keypair
from kredo.ipfs import (
    LocalIPFSProvider,
    RemotePinningProvider,
    canonical_json_full,
    fetch_document,
    get_provider,
    ipfs_enabled,
    pin_document,
)
from kredo.models import AttestorType
from kredo.signing import sign_attestation
from kredo.store import KredoStore

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cli_db(tmp_path):
    return str(tmp_path / "ipfs_test.db")


@pytest.fixture
def cli_store(cli_db):
    return KredoStore(db_path=Path(cli_db))


@pytest.fixture
def cli_identity(cli_store, cli_db):
    identity = generate_keypair("ipfs_agent", AttestorType.AGENT, cli_store)
    return identity.pubkey, cli_db


@pytest.fixture
def signed_attestation(sample_attestation, signing_key):
    """A signed attestation for IPFS tests."""
    return sign_attestation(sample_attestation, signing_key)


@pytest.fixture
def signed_attestation_dict(signed_attestation):
    """Signed attestation as a dict (what gets pinned)."""
    return json.loads(signed_attestation.model_dump_json())


@pytest.fixture
def mock_provider():
    """A mock IPFSProvider that returns a fake CID."""
    provider = MagicMock()
    provider.name = "mock"
    provider.pin.return_value = "QmFakeCid123456789abcdef"
    provider.fetch.return_value = b'{"id":"test","type":"skill_attestation"}'
    return provider


# ---------------------------------------------------------------------------
# canonical_json_full
# ---------------------------------------------------------------------------

class TestCanonicalJsonFull:
    def test_deterministic(self, signed_attestation_dict):
        """Same document produces same bytes every time."""
        a = canonical_json_full(signed_attestation_dict)
        b = canonical_json_full(signed_attestation_dict)
        assert a == b

    def test_includes_signature(self, signed_attestation_dict):
        """Signature field is included (unlike the signing path)."""
        result = canonical_json_full(signed_attestation_dict)
        parsed = json.loads(result)
        assert "signature" in parsed

    def test_sorted_keys(self):
        """Keys are sorted in output."""
        doc = {"z": 1, "a": 2, "m": {"z": 9, "a": 8}}
        result = canonical_json_full(doc)
        parsed = json.loads(result)
        assert list(parsed.keys()) == ["a", "m", "z"]
        assert list(parsed["m"].keys()) == ["a", "z"]

    def test_compact_separators(self):
        """No whitespace between separators."""
        doc = {"b": 2, "a": 1, "c": {"z": 9, "y": 8}}
        result = canonical_json_full(doc)
        assert result == b'{"a":1,"b":2,"c":{"y":8,"z":9}}'

    def test_key_order_independent(self):
        """Different key insertion order produces same bytes."""
        doc1 = {"z": 1, "a": 2, "m": 3}
        doc2 = {"a": 2, "m": 3, "z": 1}
        assert canonical_json_full(doc1) == canonical_json_full(doc2)


# ---------------------------------------------------------------------------
# ipfs_enabled
# ---------------------------------------------------------------------------

class TestIPFSEnabled:
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove the key entirely
            os.environ.pop("KREDO_IPFS_PROVIDER", None)
            assert ipfs_enabled() is False

    def test_enabled_local(self):
        with patch.dict(os.environ, {"KREDO_IPFS_PROVIDER": "local"}):
            assert ipfs_enabled() is True

    def test_enabled_remote(self):
        with patch.dict(os.environ, {"KREDO_IPFS_PROVIDER": "remote"}):
            assert ipfs_enabled() is True


# ---------------------------------------------------------------------------
# get_provider factory
# ---------------------------------------------------------------------------

class TestGetProvider:
    def test_local_provider(self):
        with patch.dict(os.environ, {"KREDO_IPFS_PROVIDER": "local"}):
            provider = get_provider()
            assert isinstance(provider, LocalIPFSProvider)
            assert provider.name == "local"

    def test_remote_provider(self):
        env = {
            "KREDO_IPFS_PROVIDER": "remote",
            "KREDO_IPFS_REMOTE_URL": "https://api.pinata.cloud",
            "KREDO_IPFS_REMOTE_TOKEN": "test_token",
        }
        with patch.dict(os.environ, env):
            provider = get_provider()
            assert isinstance(provider, RemotePinningProvider)
            assert provider.name == "remote"

    def test_unknown_provider(self):
        with patch.dict(os.environ, {"KREDO_IPFS_PROVIDER": "invalid"}):
            with pytest.raises(IPFSError, match="Unknown IPFS provider"):
                get_provider()

    def test_remote_missing_url(self):
        env = {"KREDO_IPFS_PROVIDER": "remote", "KREDO_IPFS_REMOTE_TOKEN": "tok"}
        with patch.dict(os.environ, env):
            os.environ.pop("KREDO_IPFS_REMOTE_URL", None)
            with pytest.raises(IPFSError, match="KREDO_IPFS_REMOTE_URL"):
                get_provider()

    def test_remote_missing_token(self):
        env = {"KREDO_IPFS_PROVIDER": "remote", "KREDO_IPFS_REMOTE_URL": "https://x.com"}
        with patch.dict(os.environ, env):
            os.environ.pop("KREDO_IPFS_REMOTE_TOKEN", None)
            with pytest.raises(IPFSError, match="KREDO_IPFS_REMOTE_TOKEN"):
                get_provider()


# ---------------------------------------------------------------------------
# LocalIPFSProvider
# ---------------------------------------------------------------------------

class TestLocalIPFSProvider:
    def test_default_api_url(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KREDO_IPFS_API", None)
            p = LocalIPFSProvider()
            assert "localhost:5001" in p._api

    def test_custom_api_url(self):
        p = LocalIPFSProvider(api_url="http://192.168.1.1:5001")
        assert "192.168.1.1" in p._api

    @patch("kredo.ipfs.urlopen")
    def test_pin_success(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"Hash": "QmTestCid"}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        p = LocalIPFSProvider(api_url="http://localhost:5001")
        cid = p.pin(b'{"test": true}')
        assert cid == "QmTestCid"

    @patch("kredo.ipfs.urlopen")
    def test_pin_no_hash(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        p = LocalIPFSProvider(api_url="http://localhost:5001")
        with pytest.raises(IPFSError, match="no Hash"):
            p.pin(b'{"test": true}')

    @patch("kredo.ipfs.urlopen")
    def test_fetch_success(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = b'{"id":"x"}'
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        p = LocalIPFSProvider(api_url="http://localhost:5001")
        data = p.fetch("QmTestCid")
        assert data == b'{"id":"x"}'


# ---------------------------------------------------------------------------
# RemotePinningProvider
# ---------------------------------------------------------------------------

class TestRemotePinningProvider:
    @patch("kredo.ipfs.urlopen")
    def test_pin_success(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"IpfsHash": "QmRemoteCid"}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        p = RemotePinningProvider(
            remote_url="https://api.pinata.cloud",
            remote_token="test_token",
        )
        cid = p.pin(b'{"test": true}')
        assert cid == "QmRemoteCid"

    @patch("kredo.ipfs.urlopen")
    def test_pin_hash_fallback(self, mock_urlopen):
        """Also accepts 'Hash' key (some services use this)."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"Hash": "QmAltCid"}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        p = RemotePinningProvider(
            remote_url="https://api.pinata.cloud",
            remote_token="test_token",
        )
        cid = p.pin(b'{"test": true}')
        assert cid == "QmAltCid"


# ---------------------------------------------------------------------------
# High-level pin_document / fetch_document
# ---------------------------------------------------------------------------

class TestPinDocument:
    def test_pin_with_provider(self, signed_attestation_dict, mock_provider):
        cid = pin_document(signed_attestation_dict, "attestation", mock_provider)
        assert cid == "QmFakeCid123456789abcdef"
        mock_provider.pin.assert_called_once()
        # Verify the bytes are canonical JSON
        call_data = mock_provider.pin.call_args[0][0]
        assert isinstance(call_data, bytes)
        parsed = json.loads(call_data)
        assert "signature" in parsed

    def test_pin_raises_on_failure(self, signed_attestation_dict):
        provider = MagicMock()
        provider.pin.side_effect = IPFSError("daemon down")
        with pytest.raises(IPFSError, match="daemon down"):
            pin_document(signed_attestation_dict, "attestation", provider)


class TestFetchDocument:
    def test_fetch_with_provider(self, mock_provider):
        doc = fetch_document("QmFakeCid", mock_provider)
        assert doc["id"] == "test"

    def test_fetch_invalid_json(self):
        provider = MagicMock()
        provider.fetch.return_value = b"not json"
        with pytest.raises(IPFSError, match="not valid JSON"):
            fetch_document("QmBadCid", provider)

    def test_fetch_raises_on_failure(self):
        provider = MagicMock()
        provider.fetch.side_effect = IPFSError("timeout")
        with pytest.raises(IPFSError, match="timeout"):
            fetch_document("QmBadCid", provider)


# ---------------------------------------------------------------------------
# Store — IPFS pin methods
# ---------------------------------------------------------------------------

class TestStoreIPFS:
    def test_save_and_get_pin(self, store):
        store.save_ipfs_pin("QmCid123", "att-001", "attestation", "local")
        cid = store.get_ipfs_cid("att-001")
        assert cid == "QmCid123"

    def test_get_pin_metadata(self, store):
        store.save_ipfs_pin("QmCid123", "att-001", "attestation", "local")
        pin = store.get_ipfs_pin("QmCid123")
        assert pin["document_id"] == "att-001"
        assert pin["document_type"] == "attestation"
        assert pin["provider"] == "local"
        assert pin["pinned_at"]  # non-empty timestamp

    def test_get_missing_pin(self, store):
        assert store.get_ipfs_cid("nonexistent") is None
        assert store.get_ipfs_pin("QmNope") is None

    def test_list_pins(self, store):
        store.save_ipfs_pin("QmA", "att-001", "attestation", "local")
        store.save_ipfs_pin("QmB", "rev-001", "revocation", "remote")
        pins = store.list_ipfs_pins()
        assert len(pins) == 2

    def test_list_pins_empty(self, store):
        assert store.list_ipfs_pins() == []

    def test_get_revocation(self, store, signed_attestation):
        """Test get_revocation method."""
        # First save an attestation
        raw = signed_attestation.model_dump_json()
        store.save_attestation(raw)

        # Revoke it
        from kredo.models import Revocation, Subject
        rev = Revocation(
            attestation_id=signed_attestation.id,
            revoker=Subject(pubkey=signed_attestation.attestor.pubkey),
            reason="test revocation",
        )
        from kredo.signing import sign_revocation
        from nacl.signing import SigningKey
        sk = SigningKey.generate()
        # Need to use the attestor's key — but for store test just save raw
        rev_json = rev.model_dump_json()
        store.save_revocation(rev_json)
        result = store.get_revocation(rev.id)
        assert result is not None
        assert result["id"] == rev.id

    def test_get_revocation_missing(self, store):
        assert store.get_revocation("nonexistent") is None

    def test_get_dispute(self, store, signed_attestation):
        """Test get_dispute method."""
        raw = signed_attestation.model_dump_json()
        store.save_attestation(raw)

        from kredo.models import Dispute, Subject, Evidence
        disp = Dispute(
            warning_id=signed_attestation.id,
            disputor=Subject(pubkey=signed_attestation.subject.pubkey),
            response="I disagree",
        )
        disp_json = disp.model_dump_json()
        store.save_dispute(disp_json)
        result = store.get_dispute(disp.id)
        assert result is not None
        assert result["id"] == disp.id

    def test_get_dispute_missing(self, store):
        assert store.get_dispute("nonexistent") is None


# ---------------------------------------------------------------------------
# CLI — ipfs pin
# ---------------------------------------------------------------------------

class TestIPFSCLIPin:
    def test_pin_no_ipfs_configured(self, cli_identity):
        pubkey, db = cli_identity
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KREDO_IPFS_PROVIDER", None)
            result = runner.invoke(app, ["ipfs", "pin", "some-id", "--db", db])
            assert result.exit_code == 1
            assert "not configured" in result.output

    def test_pin_document_not_found(self, cli_identity):
        pubkey, db = cli_identity
        with patch.dict(os.environ, {"KREDO_IPFS_PROVIDER": "local"}):
            with patch("kredo.cli.get_provider") as mock_gp:
                result = runner.invoke(app, ["ipfs", "pin", "nonexistent", "--db", db])
                assert result.exit_code == 1
                assert "not found" in result.output

    def test_pin_attestation_success(self, cli_store, cli_db, signed_attestation):
        raw = signed_attestation.model_dump_json()
        cli_store.save_attestation(raw)

        with patch.dict(os.environ, {"KREDO_IPFS_PROVIDER": "local"}):
            with patch("kredo.cli.get_provider") as mock_gp:
                mock_prov = MagicMock()
                mock_prov.name = "local"
                mock_prov.pin.return_value = "QmTestCid123"
                mock_gp.return_value = mock_prov

                result = runner.invoke(app, [
                    "ipfs", "pin", signed_attestation.id, "--db", cli_db,
                ])
                assert result.exit_code == 0
                assert "QmTestCid123" in result.output
                assert "Pinned" in result.output

        # Verify pin was stored
        cid = cli_store.get_ipfs_cid(signed_attestation.id)
        assert cid == "QmTestCid123"


# ---------------------------------------------------------------------------
# CLI — ipfs fetch
# ---------------------------------------------------------------------------

class TestIPFSCLIFetch:
    def test_fetch_no_ipfs_configured(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KREDO_IPFS_PROVIDER", None)
            result = runner.invoke(app, ["ipfs", "fetch", "QmTest"])
            assert result.exit_code == 1
            assert "not configured" in result.output

    def test_fetch_success_no_verify(self):
        doc = {"id": "test-123", "type": "skill_attestation", "other": "data"}
        with patch.dict(os.environ, {"KREDO_IPFS_PROVIDER": "local"}):
            with patch("kredo.cli.fetch_document", return_value=doc):
                result = runner.invoke(app, [
                    "ipfs", "fetch", "QmTest", "--no-verify",
                ])
                assert result.exit_code == 0
                assert "test-123" in result.output


# ---------------------------------------------------------------------------
# CLI — ipfs status
# ---------------------------------------------------------------------------

class TestIPFSCLIStatus:
    def test_status_no_pins(self, cli_db):
        result = runner.invoke(app, ["ipfs", "status", "--db", cli_db])
        assert result.exit_code == 0
        assert "No IPFS pins" in result.output

    def test_status_with_pins(self, cli_store, cli_db):
        cli_store.save_ipfs_pin("QmA", "att-001", "attestation", "local")
        result = runner.invoke(app, ["ipfs", "status", "--db", cli_db])
        assert result.exit_code == 0
        assert "QmA" in result.output
        assert "att-001" in result.output

    def test_status_single_doc(self, cli_store, cli_db):
        cli_store.save_ipfs_pin("QmB", "att-002", "attestation", "local")
        result = runner.invoke(app, ["ipfs", "status", "att-002", "--db", cli_db])
        assert result.exit_code == 0
        assert "QmB" in result.output

    def test_status_single_doc_not_pinned(self, cli_db):
        result = runner.invoke(app, ["ipfs", "status", "nonexistent", "--db", cli_db])
        assert result.exit_code == 0
        assert "No IPFS pin" in result.output


# ---------------------------------------------------------------------------
# CLI — submit --pin
# ---------------------------------------------------------------------------

class TestSubmitWithPin:
    def test_submit_with_pin_no_ipfs(self, cli_store, cli_db, signed_attestation):
        """--pin without IPFS configured shows warning but submit still works."""
        raw = signed_attestation.model_dump_json()
        cli_store.save_attestation(raw)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KREDO_IPFS_PROVIDER", None)
            with patch("kredo.cli._get_client") as mock_client_fn:
                mock_client = MagicMock()
                mock_client.submit_attestation.return_value = {"id": signed_attestation.id}
                mock_client.base_url = "https://api.aikredo.com"
                mock_client_fn.return_value = mock_client

                result = runner.invoke(app, [
                    "submit", signed_attestation.id, "--pin", "--db", cli_db,
                ])
                assert result.exit_code == 0
                assert "submitted" in result.output
                assert "not configured" in result.output

    def test_submit_with_pin_success(self, cli_store, cli_db, signed_attestation):
        """--pin with IPFS configured pins after successful submit."""
        raw = signed_attestation.model_dump_json()
        cli_store.save_attestation(raw)

        with patch.dict(os.environ, {"KREDO_IPFS_PROVIDER": "local"}):
            with patch("kredo.cli._get_client") as mock_client_fn:
                mock_client = MagicMock()
                mock_client.submit_attestation.return_value = {"id": signed_attestation.id}
                mock_client.base_url = "https://api.aikredo.com"
                mock_client_fn.return_value = mock_client

                with patch("kredo.cli.get_provider") as mock_gp:
                    mock_prov = MagicMock()
                    mock_prov.name = "local"
                    mock_prov.pin.return_value = "QmPinnedCid"
                    mock_gp.return_value = mock_prov

                    result = runner.invoke(app, [
                        "submit", signed_attestation.id, "--pin", "--db", cli_db,
                    ])
                    assert result.exit_code == 0
                    assert "submitted" in result.output
                    assert "QmPinnedCid" in result.output

    def test_submit_with_pin_ipfs_failure(self, cli_store, cli_db, signed_attestation):
        """IPFS failure doesn't fail the submit."""
        raw = signed_attestation.model_dump_json()
        cli_store.save_attestation(raw)

        with patch.dict(os.environ, {"KREDO_IPFS_PROVIDER": "local"}):
            with patch("kredo.cli._get_client") as mock_client_fn:
                mock_client = MagicMock()
                mock_client.submit_attestation.return_value = {"id": signed_attestation.id}
                mock_client.base_url = "https://api.aikredo.com"
                mock_client_fn.return_value = mock_client

                with patch("kredo.cli.get_provider") as mock_gp:
                    mock_prov = MagicMock()
                    mock_prov.name = "local"
                    mock_prov.pin.side_effect = IPFSError("daemon unreachable")
                    mock_gp.return_value = mock_prov

                    result = runner.invoke(app, [
                        "submit", signed_attestation.id, "--pin", "--db", cli_db,
                    ])
                    assert result.exit_code == 0
                    assert "submitted" in result.output
                    assert "pin failed" in result.output.lower()


# ---------------------------------------------------------------------------
# Evidence — ipfs: URI pattern
# ---------------------------------------------------------------------------

class TestIPFSEvidencePattern:
    def test_ipfs_uri_recognized(self):
        """ipfs: URIs should be recognized as verifiable artifacts."""
        from kredo.evidence import _URI_PATTERNS
        test_uri = "ipfs:QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG"
        matched = any(p.match(test_uri) for p in _URI_PATTERNS)
        assert matched, f"ipfs: URI not matched by any pattern"

    def test_ipfs_uri_boosts_verifiability(self):
        """Evidence with ipfs: artifacts should score higher on verifiability."""
        from kredo.evidence import score_evidence
        from kredo.models import AttestationType, Evidence

        ev_without = Evidence(
            context="Test context for evidence scoring",
            artifacts=["some-opaque-reference"],
        )
        ev_with = Evidence(
            context="Test context for evidence scoring",
            artifacts=["ipfs:QmTestCid123"],
        )
        score_without = score_evidence(ev_without, AttestationType.SKILL)
        score_with = score_evidence(ev_with, AttestationType.SKILL)
        assert score_with.verifiability >= score_without.verifiability


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class TestIPFSError:
    def test_is_kredo_error(self):
        from kredo.exceptions import KredoError
        err = IPFSError("test")
        assert isinstance(err, KredoError)

    def test_importable_from_kredo(self):
        from kredo import IPFSError as IE
        assert IE is IPFSError
