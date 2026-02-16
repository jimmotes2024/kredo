"""Tests for kredo.signing — sign/verify for attestations, disputes, revocations."""

from datetime import datetime, timedelta, timezone

import pytest
from nacl.signing import SigningKey

from kredo._canonical import canonical_json
from kredo.exceptions import InvalidSignatureError
from kredo.models import (
    AttestationType,
    AttestorType,
    Attestation,
    Attestor,
    Dispute,
    Evidence,
    Proficiency,
    Revocation,
    Skill,
    Subject,
)
from kredo.signing import (
    sign_attestation,
    sign_dispute,
    sign_revocation,
    verify_attestation,
    verify_dispute,
    verify_revocation,
)
from tests.conftest import _pubkey


class TestCanonicalJson:
    def test_deterministic(self):
        data = {"b": 2, "a": 1, "c": {"z": 3, "y": 4}}
        assert canonical_json(data) == canonical_json(data)

    def test_sorted_keys(self):
        result = canonical_json({"b": 2, "a": 1})
        assert result == b'{"a":1,"b":2}'

    def test_no_whitespace(self):
        result = canonical_json({"key": "value"})
        assert b" " not in result

    def test_none_excluded(self):
        result = canonical_json({"a": 1, "b": None})
        assert result == b'{"a":1}'

    def test_datetime_iso(self):
        dt = datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc)
        result = canonical_json({"t": dt})
        assert result == b'{"t":"2026-02-14T12:00:00Z"}'


class TestSignVerifyAttestation:
    def test_roundtrip(self, signing_key, sample_attestation):
        signed = sign_attestation(sample_attestation, signing_key)
        assert signed.signature is not None
        assert signed.signature.startswith("ed25519:")
        assert verify_attestation(signed) is True

    def test_tamper_detection(self, signing_key, sample_attestation):
        signed = sign_attestation(sample_attestation, signing_key)
        # Tamper with the evidence
        tampered = signed.model_copy(
            update={"evidence": Evidence(context="tampered context")}
        )
        with pytest.raises(InvalidSignatureError):
            verify_attestation(tampered)

    def test_wrong_key_rejected(self, signing_key, signing_key_b, sample_attestation):
        with pytest.raises(InvalidSignatureError, match="does not match"):
            sign_attestation(sample_attestation, signing_key_b)

    def test_unsigned_rejected(self, sample_attestation):
        with pytest.raises(InvalidSignatureError, match="no signature"):
            verify_attestation(sample_attestation)


class TestSignVerifyDispute:
    def test_roundtrip(self, signing_key, pubkey):
        dispute = Dispute(
            warning_id="warn-123",
            disputor=Subject(pubkey=pubkey, name="Disputor"),
            response="I did not do this",
        )
        signed = sign_dispute(dispute, signing_key)
        assert verify_dispute(signed) is True

    def test_tamper_detection(self, signing_key, pubkey):
        dispute = Dispute(
            warning_id="warn-123",
            disputor=Subject(pubkey=pubkey, name="Disputor"),
            response="I did not do this",
        )
        signed = sign_dispute(dispute, signing_key)
        tampered = signed.model_copy(update={"response": "actually I did"})
        with pytest.raises(InvalidSignatureError):
            verify_dispute(tampered)


class TestSignVerifyRevocation:
    def test_roundtrip(self, signing_key, pubkey):
        rev = Revocation(
            attestation_id="att-456",
            revoker=Subject(pubkey=pubkey, name="Revoker"),
            reason="No longer valid",
        )
        signed = sign_revocation(rev, signing_key)
        assert verify_revocation(signed) is True

    def test_tamper_detection(self, signing_key, pubkey):
        rev = Revocation(
            attestation_id="att-456",
            revoker=Subject(pubkey=pubkey, name="Revoker"),
            reason="No longer valid",
        )
        signed = sign_revocation(rev, signing_key)
        tampered = signed.model_copy(update={"reason": "changed reason"})
        with pytest.raises(InvalidSignatureError):
            verify_revocation(tampered)
