"""Tests for kredo.models — schema validation and construction."""

from datetime import datetime, timedelta, timezone

import pytest

from kredo.models import (
    AttestationType,
    AttestorType,
    Attestation,
    Attestor,
    Dispute,
    Evidence,
    Identity,
    Proficiency,
    Revocation,
    Skill,
    Subject,
    WarningCategory,
)


def _now():
    return datetime.now(timezone.utc)


def _make_pubkey(n=0):
    return "ed25519:" + f"{n:064x}"


class TestSubject:
    def test_valid(self):
        s = Subject(pubkey=_make_pubkey(), name="Alice")
        assert s.name == "Alice"

    def test_invalid_pubkey_prefix(self):
        with pytest.raises(ValueError, match="ed25519:"):
            Subject(pubkey="rsa:abc", name="x")

    def test_invalid_pubkey_length(self):
        with pytest.raises(ValueError, match="64 hex characters"):
            Subject(pubkey="ed25519:abcd", name="x")

    def test_invalid_pubkey_hex(self):
        with pytest.raises(ValueError, match="hexadecimal"):
            Subject(pubkey="ed25519:" + "g" * 64, name="x")


class TestAttestor:
    def test_valid(self):
        a = Attestor(pubkey=_make_pubkey(), name="Bot", type=AttestorType.AGENT)
        assert a.type == AttestorType.AGENT


class TestSkill:
    def test_valid(self):
        s = Skill(domain="security-operations", specific="incident-triage", proficiency=Proficiency.EXPERT)
        assert s.proficiency == Proficiency.EXPERT

    def test_invalid_domain(self):
        with pytest.raises(ValueError, match="Unknown domain"):
            Skill(domain="fake-domain", specific="thing", proficiency=Proficiency.NOVICE)

    def test_invalid_skill(self):
        with pytest.raises(ValueError, match="Unknown skill"):
            Skill(domain="security-operations", specific="fake-skill", proficiency=Proficiency.NOVICE)


class TestAttestation:
    def test_skill_attestation(self, sample_attestation):
        assert sample_attestation.type == AttestationType.SKILL
        assert sample_attestation.skill is not None

    def test_warning_attestation(self, sample_warning):
        assert sample_warning.type == AttestationType.WARNING
        assert sample_warning.warning_category == WarningCategory.SPAM

    def test_expires_before_issued(self):
        now = _now()
        with pytest.raises(ValueError, match="expires must be after issued"):
            Attestation(
                type=AttestationType.SKILL,
                subject=Subject(pubkey=_make_pubkey(1), name="x"),
                attestor=Attestor(pubkey=_make_pubkey(2), name="y", type=AttestorType.AGENT),
                skill=Skill(domain="reasoning", specific="planning", proficiency=Proficiency.COMPETENT),
                evidence=Evidence(context="test"),
                issued=now,
                expires=now - timedelta(days=1),
            )

    def test_warning_requires_category(self):
        now = _now()
        with pytest.raises(ValueError, match="require a category"):
            Attestation(
                type=AttestationType.WARNING,
                subject=Subject(pubkey=_make_pubkey(1), name="x"),
                attestor=Attestor(pubkey=_make_pubkey(2), name="y", type=AttestorType.AGENT),
                evidence=Evidence(context="A" * 200, artifacts=["log:1"]),
                issued=now,
                expires=now + timedelta(days=365),
            )

    def test_warning_requires_artifacts(self):
        now = _now()
        with pytest.raises(ValueError, match="verifiable evidence"):
            Attestation(
                type=AttestationType.WARNING,
                subject=Subject(pubkey=_make_pubkey(1), name="x"),
                attestor=Attestor(pubkey=_make_pubkey(2), name="y", type=AttestorType.AGENT),
                warning_category=WarningCategory.SPAM,
                evidence=Evidence(context="A" * 200, artifacts=[]),
                issued=now,
                expires=now + timedelta(days=365),
            )

    def test_warning_requires_long_context(self):
        now = _now()
        with pytest.raises(ValueError, match="at least 100 characters"):
            Attestation(
                type=AttestationType.WARNING,
                subject=Subject(pubkey=_make_pubkey(1), name="x"),
                attestor=Attestor(pubkey=_make_pubkey(2), name="y", type=AttestorType.AGENT),
                warning_category=WarningCategory.SPAM,
                evidence=Evidence(context="short", artifacts=["log:1"]),
                issued=now,
                expires=now + timedelta(days=365),
            )

    def test_non_warning_requires_skill(self):
        now = _now()
        with pytest.raises(ValueError, match="requires a skill field"):
            Attestation(
                type=AttestationType.SKILL,
                subject=Subject(pubkey=_make_pubkey(1), name="x"),
                attestor=Attestor(pubkey=_make_pubkey(2), name="y", type=AttestorType.AGENT),
                evidence=Evidence(context="test"),
                issued=now,
                expires=now + timedelta(days=365),
            )

    def test_serialization_roundtrip(self, sample_attestation):
        json_str = sample_attestation.model_dump_json()
        restored = Attestation.model_validate_json(json_str)
        assert restored.id == sample_attestation.id
        assert restored.type == sample_attestation.type


class TestDispute:
    def test_valid(self):
        d = Dispute(
            warning_id="some-warning-id",
            disputor=Subject(pubkey=_make_pubkey(), name="Disputed"),
            response="I didn't do that",
        )
        assert d.warning_id == "some-warning-id"


class TestRevocation:
    def test_valid(self):
        r = Revocation(
            attestation_id="some-att-id",
            revoker=Subject(pubkey=_make_pubkey(), name="Revoker"),
            reason="I no longer stand by this attestation",
        )
        assert r.attestation_id == "some-att-id"


class TestIdentity:
    def test_valid(self):
        i = Identity(
            pubkey=_make_pubkey(),
            name="TestAgent",
            type=AttestorType.AGENT,
        )
        assert i.type == AttestorType.AGENT
